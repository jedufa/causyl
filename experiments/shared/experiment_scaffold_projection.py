"""Shared projection rules for deterministic experiment scaffolds.

This module owns the seam between CLI inputs and the generated experiment
contract plus starter docs. It keeps placeholder rules centralized so scaffold
templates do not grow hidden business logic.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence


PROJECT_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]*$")
SLUG_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]*$")

DEFAULT_CONTEXT_BUNDLE = "lab_core_v1"
DEFAULT_RESOURCE_CLASS = "cpu_light"
DEFAULT_ANALYSIS_RECIPE = "summary_report"
DEFAULT_PROFILE_ID = "starter"
DEFAULT_RUNNER_ID = "default"


def _repo_root(repo_root: Optional[Path] = None) -> Path:
    if repo_root is not None:
        return Path(repo_root).resolve()
    return Path(__file__).resolve().parents[2]


def normalize_project_id(project_id: str) -> str:
    text = str(project_id or "").strip().lower()
    if not text:
        raise ValueError("project_id is required")
    if not PROJECT_ID_PATTERN.match(text):
        raise ValueError("project_id must use lowercase letters, digits, hyphen, or underscore")
    return text


def normalize_display_name(project_id: str, display_name: str) -> str:
    text = str(display_name or "").strip()
    if text:
        return text
    parts = [part for part in re.split(r"[-_]+", project_id) if part]
    return " ".join(part.capitalize() for part in parts) or project_id


def normalize_owner(owner: str) -> str:
    return str(owner or "").strip()


def normalize_tags(tags: Sequence[str] | None) -> List[str]:
    items = {str(tag or "").strip().lower() for tag in list(tags or [])}
    items.discard("")
    return sorted(items)


def normalize_slug_field(value: str, *, field_name: str, default: str) -> str:
    text = str(value or "").strip().lower() or default
    if not SLUG_PATTERN.match(text):
        raise ValueError(f"{field_name} must use lowercase letters, digits, hyphen, or underscore")
    return text


def default_external_data_root(project_id: str) -> str:
    return f"~/causyl_experiment_data/{project_id}"


def project_root_relative(project_id: str) -> str:
    return f"experiments/projects/{project_id}"


def default_project_semaphore(project_id: str, resource_class: str) -> str:
    prefix = "resource"
    if resource_class.startswith("gpu"):
        prefix = "gpu"
    elif resource_class.startswith("cpu"):
        prefix = "cpu"
    return f"{prefix}/{project_id}"


def validate_external_data_root(
    external_data_root: str,
    *,
    project_id: str,
    repo_root: Optional[Path] = None,
) -> str:
    text = str(external_data_root or "").strip() or default_external_data_root(project_id)
    root = _repo_root(repo_root)
    expanded = Path(text).expanduser()
    resolved = (root / expanded).resolve() if not expanded.is_absolute() else expanded.resolve()
    if resolved == root or root in resolved.parents:
        raise ValueError("external_data_root must live outside the tracked repo root")
    return text


def build_placeholder_completion_rules(
    *,
    project_id: str,
    default_profile: str,
    analysis_recipe: str,
    resource_class: str,
    required_context_bundle: str,
    external_data_root: str,
) -> List[Dict[str, Any]]:
    project_root = project_root_relative(project_id)
    return [
        {
            "rule_id": "launch_placeholder",
            "fields": [
                "launch.entrypoints.default.command",
                "launch.entrypoints.default.working_dir",
                "launch.default_runner_id",
            ],
            "severity": "blocker",
            "reason": "The scaffold ships a placeholder runner only.",
            "completion_required": "Replace _runner/run_experiment.py with a real launch entrypoint before scheduling non-dry runs.",
        },
        {
            "rule_id": "registry_placeholder",
            "fields": [
                "configuration.registry_root",
                f"{project_root}/_spec/registry/v1/{default_profile}.yaml",
            ],
            "severity": "blocker",
            "reason": "The starter registry/spec file is intentionally incomplete.",
            "completion_required": "Fill the starter registry/spec definitions before using the scaffold for real experiment execution.",
        },
        {
            "rule_id": "resource_placeholder",
            "fields": [
                "resources.resource_class",
                "resources.project_semaphore",
                "resources.max_parallel_runs",
                "resources.estimated_footprint",
            ],
            "severity": "blocker",
            "reason": f"The default resource posture ({resource_class}) is a starter guess, not an earned runtime guarantee.",
            "completion_required": "Review the resource class, semaphore, parallelism, and footprint against the real workload before enabling scheduled runs.",
        },
        {
            "rule_id": "analysis_placeholder",
            "fields": [
                "analysis.primary_artifacts",
                "analysis.recipes",
                "analysis.default_recipe",
                f"analysis/{analysis_recipe}.md",
            ],
            "severity": "blocker",
            "reason": "The scaffold reserves one analysis entrypoint without claiming scientific completeness.",
            "completion_required": "Replace the analysis recipe placeholder and map primary artifacts honestly before treating analysis outputs as meaningful.",
        },
        {
            "rule_id": "context_bundle_confirmation",
            "fields": ["context_policy.required_context_bundle"],
            "severity": "blocker",
            "reason": f"The scaffold defaults to {required_context_bundle} until a stronger experiment-specific context policy is declared.",
            "completion_required": "Confirm the required context bundle and any forbidden refs before using the scaffold in automated runs.",
        },
        {
            "rule_id": "artifact_boundary_confirmation",
            "fields": [
                "artifacts.external_data_root",
                "artifacts.repo_tracked_outputs",
                f"{project_root}/_artifacts/data_root_policy.json",
            ],
            "severity": "blocker",
            "reason": f"Heavy or mutable runtime artifacts must live under {external_data_root}, not inside the tracked repo tree.",
            "completion_required": "Keep only metadata-safe placeholders in git and direct heavy outputs to the external data root.",
        },
        {
            "rule_id": "profile_placeholder",
            "fields": [
                "configuration.default_profile",
                f"{project_root}/_config/profiles/{default_profile}.json",
            ],
            "severity": "blocker",
            "reason": "The starter profile is a completion checkpoint, not a validated operational profile.",
            "completion_required": "Replace placeholder values in the starter profile before using it as the default for scheduled execution.",
        },
        {
            "rule_id": "lifecycle_run_record_placeholder",
            "fields": [
                f"{project_root}/_lifecycle/run_record.placeholder.json",
                "run_record.json",
            ],
            "severity": "blocker",
            "reason": "The scaffold reserves the canonical run-record filename without pretending the project already owns live run truth.",
            "completion_required": "Adopt the canonical run lifecycle contract before replacing the placeholder pack with real per-run run_record.json writes.",
        },
        {
            "rule_id": "lifecycle_checkpoint_placeholder",
            "fields": [
                f"{project_root}/_lifecycle/resume_from.placeholder.json",
                f"{project_root}/_lifecycle/checkpoint_bundles_v2/README.md",
            ],
            "severity": "blocker",
            "reason": "Checkpoint lineage seams are reserved early so later adoption stays additive instead of inventing new folder law.",
            "completion_required": "Bind checkpoint lineage and resume pointers to the canonical lifecycle truth family before claiming resumability.",
        },
        {
            "rule_id": "lifecycle_intervention_placeholder",
            "fields": [
                f"{project_root}/_lifecycle/run_record.interventions.placeholder.jsonl",
                "run_record.interventions.jsonl",
            ],
            "severity": "blocker",
            "reason": "Intervention history needs an explicit seam from day one, even when the scaffold is still launch-incomplete.",
            "completion_required": "Replace the intervention placeholder with append-only lifecycle-truth writes before surfacing operator intervention history as durable truth.",
        },
    ]


def build_scaffold_projection(
    project_id: str,
    *,
    display_name: str = "",
    owner: str = "",
    tags: Sequence[str] | None = None,
    family_tags: Sequence[str] | None = None,
    default_profile: str = DEFAULT_PROFILE_ID,
    analysis_recipe: str = DEFAULT_ANALYSIS_RECIPE,
    required_context_bundle: str = DEFAULT_CONTEXT_BUNDLE,
    resource_class: str = DEFAULT_RESOURCE_CLASS,
    external_data_root: str = "",
    repo_root: Optional[Path] = None,
) -> Dict[str, Any]:
    project_key = normalize_project_id(project_id)
    display = normalize_display_name(project_key, display_name)
    owner_text = normalize_owner(owner)
    tag_list = normalize_tags(tags)
    family_tag_list = normalize_tags(family_tags)
    profile_id = normalize_slug_field(default_profile, field_name="default_profile", default=DEFAULT_PROFILE_ID)
    recipe_id = normalize_slug_field(analysis_recipe, field_name="analysis_recipe", default=DEFAULT_ANALYSIS_RECIPE)
    bundle = str(required_context_bundle or "").strip() or DEFAULT_CONTEXT_BUNDLE
    resource = normalize_slug_field(resource_class, field_name="resource_class", default=DEFAULT_RESOURCE_CLASS)
    external_root = validate_external_data_root(external_data_root, project_id=project_key, repo_root=repo_root)
    project_root_rel = project_root_relative(project_key)
    semaphore = default_project_semaphore(project_key, resource)
    placeholder_rules = build_placeholder_completion_rules(
        project_id=project_key,
        default_profile=profile_id,
        analysis_recipe=recipe_id,
        resource_class=resource,
        required_context_bundle=bundle,
        external_data_root=external_root,
    )
    note_projection = {
        "default_profile": profile_id,
        "analysis_recipe": recipe_id,
        "required_context_bundle": bundle,
        "resource_class": resource,
        "project_semaphore": semaphore,
        "external_data_root": external_root,
        "max_parallel_runs": 1,
    }
    return {
        "project_id": project_key,
        "display_name": display,
        "owner": owner_text,
        "tags": tag_list,
        "family_tags": family_tag_list,
        "default_profile": profile_id,
        "analysis_recipe": recipe_id,
        "required_context_bundle": bundle,
        "resource_class": resource,
        "project_root_relative": project_root_rel,
        "project_semaphore": semaphore,
        "external_data_root": external_root,
        "default_runner_id": DEFAULT_RUNNER_ID,
        "placeholder_completion_rules": placeholder_rules,
        "completion_required": [row["completion_required"] for row in placeholder_rules],
        "note_projection": note_projection,
        "projection_summary": {
            "project_id": project_key,
            "display_name": display,
            "default_profile": profile_id,
            "analysis_recipe": recipe_id,
            "required_context_bundle": bundle,
            "resource_class": resource,
            "project_semaphore": semaphore,
            "external_data_root": external_root,
            "family_tags": family_tag_list,
        },
    }
