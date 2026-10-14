from __future__ import annotations

import os
from typing import Any, Dict, List, Mapping

KNOWN_AGENT_TECHS = {"codex", "openai", "claude", "gemini", "xai", "ollama", "deepseek"}
EXECUTION_MODES = {"live", "fixture", "disabled"}
STAGE8_EXECUTION_MODE_ENV_KEYS = (
    "PIPELINE_STAGES__SCRIPT_8_PATCH_GENERATE__EXECUTION_MODE",
    "PIPELINE__STAGES__SCRIPT_8_PATCH_GENERATE__EXECUTION_MODE",
)
STAGE8_ALLOW_LIVE_ENV_KEYS = (
    "PIPELINE_STAGES__SCRIPT_8_PATCH_GENERATE__ALLOW_LIVE_MODEL_CALLS",
    "PIPELINE__STAGES__SCRIPT_8_PATCH_GENERATE__ALLOW_LIVE_MODEL_CALLS",
)


def _env_value(keys: List[str] | tuple[str, ...]) -> str:
    for key in keys:
        value = str(os.environ.get(key) or "").strip()
        if value:
            return value
    return ""


def _env_truthy(value: str) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def infer_agent_tech_from_model(model: str) -> str:
    name = str(model or "").strip().lower()
    if not name:
        return ""
    if name == "gpt-5.3-codex-spark" or "codex" in name:
        return "codex"
    if name.startswith(("gpt-", "o1", "o3", "o4")):
        return "openai"
    if "claude" in name:
        return "claude"
    if "gemini" in name:
        return "gemini"
    if "grok" in name:
        return "xai"
    if name.startswith("deepseek"):
        return "deepseek"
    if any(token in name for token in ("qwen", "llama", "mistral", "phi", "gemma")):
        return "ollama"
    return ""


def _provider_for_agent_tech(agent_tech: str) -> str:
    tech = str(agent_tech or "").strip().lower()
    if tech == "codex":
        return "openai"
    if tech in {"openai", "claude", "gemini", "xai", "ollama", "deepseek"}:
        return tech
    return ""


def _resolved_agent_tech_for_model(requested_agent_tech: str, model: str) -> str:
    requested = str(requested_agent_tech or "").strip().lower()
    inferred = infer_agent_tech_from_model(model)
    if not inferred:
        return requested
    if not requested:
        return inferred
    if requested == inferred:
        return requested
    if requested in {"openai", "codex"} and inferred in {"openai", "codex"}:
        return requested
    return inferred


def _resolved_provider_for_model(
    requested_provider: str,
    *,
    model: str,
    agent_tech: str = "",
    default_provider: str = "",
) -> str:
    requested = str(requested_provider or "").strip().lower()
    resolved_agent_tech = _resolved_agent_tech_for_model(agent_tech, model)
    inferred_provider = _provider_for_agent_tech(resolved_agent_tech)
    if inferred_provider:
        return inferred_provider
    fallback = str(default_provider or "").strip().lower()
    return requested or fallback


def resolve_stage8_runtime(config: Mapping[str, Any]) -> Dict[str, Any]:
    stage_cfg = dict(config.get("_stage") or {})
    planning = dict(config.get("planning") or {})
    models = dict(config.get("models") or {})
    timeouts = dict(config.get("timeouts") or {})

    requested_model = str(
        stage_cfg.get("model")
        or stage_cfg.get("requested_model")
        or planning.get("model_name")
        or "gpt-5.4"
    ).strip()
    requested_agent_tech = str(stage_cfg.get("agent_tech") or stage_cfg.get("requested_agent_tech") or "").strip().lower()
    inferred_agent_tech = infer_agent_tech_from_model(requested_model)
    selected_agent_tech = _resolved_agent_tech_for_model(requested_agent_tech, requested_model)
    selected_model = requested_model
    provider = _resolved_provider_for_model(
        str(stage_cfg.get("provider") or "").strip().lower(),
        model=selected_model,
        agent_tech=selected_agent_tech or requested_agent_tech,
        default_provider=str(models.get("default_remote_provider") or "openai").strip().lower() or "openai",
    )
    execution_mode = str(stage_cfg.get("execution_mode") or "fixture").strip().lower() or "fixture"
    if execution_mode not in EXECUTION_MODES:
        execution_mode = "fixture"
    allow_live_model_calls = bool(stage_cfg.get("allow_live_model_calls", False))
    explicit_execution_mode_override = _env_value(STAGE8_EXECUTION_MODE_ENV_KEYS)
    explicit_allow_live_override = _env_value(STAGE8_ALLOW_LIVE_ENV_KEYS)
    if explicit_allow_live_override:
        allow_live_model_calls = _env_truthy(explicit_allow_live_override)
    elif explicit_execution_mode_override and execution_mode == "live":
        allow_live_model_calls = False
    fallback_models = [str(item).strip() for item in list(stage_cfg.get("fallback_models") or []) if str(item).strip()]
    fallback_agent_techs = [str(item).strip().lower() for item in list(stage_cfg.get("fallback_agent_techs") or []) if str(item).strip()]
    timeout_seconds = int(stage_cfg.get("timeout_seconds") or timeouts.get("model_seconds") or 3000)
    reasoning_effort = str(stage_cfg.get("reasoning_effort") or "").strip() or None
    temperature = float(stage_cfg.get("temperature") or 0)
    max_output_tokens = int(stage_cfg.get("max_output_tokens") or 4000)
    preferred_artifact = str(stage_cfg.get("preferred_artifact") or "unified_diff").strip() or "unified_diff"

    runtime = {
        "requested_model": requested_model,
        "requested_agent_tech": requested_agent_tech,
        "selected_model": selected_model,
        "selected_agent_tech": selected_agent_tech,
        "model": requested_model,
        "agent_tech": selected_agent_tech or requested_agent_tech,
        "provider": provider,
        "execution_mode": execution_mode,
        "allow_live_model_calls": allow_live_model_calls,
        "fallback_models": fallback_models,
        "fallback_agent_techs": fallback_agent_techs,
        "timeout_seconds": timeout_seconds,
        "reasoning_effort": reasoning_effort,
        "temperature": temperature,
        "max_output_tokens": max_output_tokens,
        "preferred_artifact": preferred_artifact,
        "selected_via": (
            "explicit_agent_tech"
            if requested_agent_tech and selected_agent_tech == requested_agent_tech
            else ("model_family_inference" if selected_agent_tech else "unresolved")
        ),
        "runtime_summary": f"{provider}:{selected_agent_tech or 'unknown'}:{selected_model}".strip(":"),
    }
    return runtime
