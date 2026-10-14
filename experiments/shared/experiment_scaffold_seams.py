"""Contract-to-scaffold seam verification and bootstrap walkthrough helpers.

These helpers keep the ownership boundary explicit:
- experiment contract = canonical semantic launch/config truth
- scaffold manifest = generator-owned structural/layout truth
- discovery registry = narrow listing/resolution truth

They do not settle run-lifecycle truth, portfolio policy, or deep operator UI
contracts.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence

from .experiment_contract import read_experiment_contract
from .experiment_registry import REGISTRY_RELATIVE_PATH
from .experiment_registry import REGISTRY_ARTIFACT_BOUNDARY_SCHEMA_VERSION
from .experiment_registry import REGISTRY_COMPATIBILITY_SCHEMA_VERSION
from .experiment_registry import resolve_registered_experiment
from .experiment_scaffold import FILE_OWNERSHIP_SET
from .experiment_scaffold import GENERATOR_ENTRYPOINT
from .experiment_scaffold import LAYOUT_CONTRACT_PATH
from .experiment_scaffold import PLACEHOLDER_FILE_KINDS
from .experiment_scaffold import RUN_LIFECYCLE_CONTRACT_PATH
from .experiment_scaffold import SCAFFOLD_MANIFEST_SCHEMA_VERSION
from .experiment_scaffold import SCAFFOLD_VERSION_CONTRACT_SCHEMA_VERSION
from .experiment_scaffold import SCAFFOLD_VERSION
from .experiment_scaffold import SUPPORTED_COLLISION_POLICIES
from .experiment_scaffold import experiment_project_root
from .experiment_scaffold import experiment_scaffold_manifest_path
from .experiment_scaffold import write_experiment_scaffold


SEAM_CONTRACT_PATH = "docs/20_contracts/experiments/Contract_Experiment_Contract_Scaffold_Seams_V1.html"
BOOTSTRAP_WALKTHROUGH_DOC_PATH = "docs/10_operator/manuals/Experiment_Bootstrap_Walkthrough.html"
MIGRATION_NOTES_PATH = "docs/60_reports/Experiment_Definition_Foundation_Migration_Notes.html"


def _repo_root(repo_root: Optional[Path] = None) -> Path:
    if repo_root is not None:
        return Path(repo_root).resolve()
    return Path(__file__).resolve().parents[2]


def _display_path(path: Path, *, repo_root: Path) -> str:
    try:
        return path.resolve().relative_to(repo_root.resolve()).as_posix()
    except ValueError:
        return str(path.resolve())


def _read_manifest_payload(path: Path) -> Dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if str(data.get("schema_version") or "").strip() != SCAFFOLD_MANIFEST_SCHEMA_VERSION:
        raise ValueError("unsupported scaffold manifest schema_version")
    if str(data.get("scaffold_version") or "").strip() != SCAFFOLD_VERSION:
        raise ValueError("unsupported scaffold_version")
    generated_files = data.get("generated_files")
    if not isinstance(generated_files, list):
        raise ValueError("scaffold manifest is missing generated_files")
    placeholder_files = data.get("placeholder_files")
    if placeholder_files is None:
        placeholder_files = [
            {
                "path": str(row.get("path") or ""),
                "kind": str(row.get("kind") or ""),
                "ownership": str(row.get("ownership") or ""),
                "completion_mode": "replace_or_fill_before_real_runs",
            }
            for row in generated_files
            if isinstance(row, dict) and str(row.get("kind") or "") in PLACEHOLDER_FILE_KINDS
        ]
        data["placeholder_files"] = [row for row in placeholder_files if row["path"]]
    elif not isinstance(placeholder_files, list):
        raise ValueError("scaffold manifest is missing placeholder_files")
    if not isinstance(data.get("reserved_paths"), list):
        raise ValueError("scaffold manifest is missing reserved_paths")
    if not isinstance(data.get("lifecycle_seams"), list):
        raise ValueError("scaffold manifest is missing lifecycle_seams")
    return data


def read_experiment_scaffold_manifest(project_id: str, *, repo_root: Optional[Path] = None) -> Dict[str, Any]:
    root = _repo_root(repo_root)
    path = experiment_scaffold_manifest_path(project_id, repo_root=root)
    if not path.exists():
        return {
            "ok": False,
            "error": f"scaffold manifest not found: {_display_path(path, repo_root=root)}",
            "code": "scaffold_manifest_not_found",
            "manifest_path": _display_path(path, repo_root=root),
        }
    try:
        payload = _read_manifest_payload(path)
    except Exception as exc:
        return {
            "ok": False,
            "error": str(exc),
            "code": "invalid_scaffold_manifest",
            "manifest_path": _display_path(path, repo_root=root),
        }
    return {
        "ok": True,
        "manifest_path": _display_path(path, repo_root=root),
        "manifest": payload,
    }


def verify_experiment_contract_scaffold_seams(project_id: str, *, repo_root: Optional[Path] = None) -> Dict[str, Any]:
    root = _repo_root(repo_root)
    project_key = str(project_id or "").strip().lower()
    errors: List[Dict[str, str]] = []
    warnings: List[Dict[str, str]] = []

    registry_result = resolve_registered_experiment(project_key, repo_root=root, verify_paths=True)
    manifest_result = read_experiment_scaffold_manifest(project_key, repo_root=root)
    contract_result = read_experiment_contract(project_key, repo_root=root)

    if not registry_result.get("ok"):
        errors.append({"field": "registry", "message": str(registry_result.get("error") or "registry resolution failed")})
    if not manifest_result.get("ok"):
        errors.append({"field": "scaffold_manifest", "message": str(manifest_result.get("error") or "scaffold manifest read failed")})
    if not contract_result.get("ok"):
        errors.append({"field": "experiment_contract", "message": str(contract_result.get("error") or "contract read failed")})

    record = registry_result.get("record") if isinstance(registry_result.get("record"), dict) else {}
    manifest = manifest_result.get("manifest") if isinstance(manifest_result.get("manifest"), dict) else {}
    contract = contract_result.get("contract") if isinstance(contract_result.get("contract"), dict) else {}

    project_root_rel = str(record.get("project_root") or f"experiments/projects/{project_key}").strip()
    expected_contract_path = f"{project_root_rel}/_runner/experiment_contract.json"
    expected_manifest_path = f"{project_root_rel}/_scaffold/scaffold_manifest.json"
    expected_project_runner_manifest = f"{project_root_rel}/_runner/project_runner_manifest.json"
    expected_data_root_policy = f"{project_root_rel}/_artifacts/data_root_policy.json"
    expected_lifecycle_readme = f"{project_root_rel}/_lifecycle/README.md"
    expected_run_record_placeholder = f"{project_root_rel}/_lifecycle/run_record.placeholder.json"
    expected_interventions_placeholder = f"{project_root_rel}/_lifecycle/run_record.interventions.placeholder.jsonl"
    expected_resume_placeholder = f"{project_root_rel}/_lifecycle/resume_from.placeholder.json"
    expected_checkpoint_placeholder = f"{project_root_rel}/_lifecycle/checkpoint_bundles_v2/README.md"

    if record:
        if str(record.get("contract_path") or "") != expected_contract_path:
            errors.append({"field": "registry.contract_path", "message": f"registry contract_path must be {expected_contract_path}"})
        if str(record.get("scaffold_manifest_path") or "") != expected_manifest_path:
            errors.append({"field": "registry.scaffold_manifest_path", "message": f"registry scaffold_manifest_path must be {expected_manifest_path}"})
        if str(record.get("discovery_status") or "") != "registered":
            warnings.append({"field": "registry.discovery_status", "message": "registered experiment should report discovery_status=registered"})
        artifact_boundary = record.get("artifact_boundary") if isinstance(record.get("artifact_boundary"), dict) else {}
        if not artifact_boundary:
            errors.append({"field": "registry.artifact_boundary", "message": "registry artifact_boundary metadata is required"})
        else:
            if str(artifact_boundary.get("schema_version") or "") != REGISTRY_ARTIFACT_BOUNDARY_SCHEMA_VERSION:
                errors.append(
                    {
                        "field": "registry.artifact_boundary.schema_version",
                        "message": f"registry artifact_boundary.schema_version must be {REGISTRY_ARTIFACT_BOUNDARY_SCHEMA_VERSION}",
                    }
                )
            if str(artifact_boundary.get("policy_path") or "") != expected_data_root_policy:
                errors.append(
                    {
                        "field": "registry.artifact_boundary.policy_path",
                        "message": f"registry artifact_boundary.policy_path must be {expected_data_root_policy}",
                    }
                )
            if str(artifact_boundary.get("external_data_root") or "") != str(record.get("external_data_root") or ""):
                errors.append(
                    {
                        "field": "registry.artifact_boundary.external_data_root",
                        "message": "registry artifact_boundary.external_data_root must match registry external_data_root",
                    }
                )
            repo_tracked_placeholders = {
                str(item or "").strip()
                for item in list(artifact_boundary.get("repo_tracked_placeholders") or [])
                if str(item or "").strip()
            }
            required_repo_tracked = {
                f"{project_root_rel}/_artifacts/README.md",
                expected_data_root_policy,
                f"{project_root_rel}/analysis/{str(record.get('analysis_recipe') or '')}.md",
            }
            missing_repo_tracked = sorted(path for path in required_repo_tracked if path not in repo_tracked_placeholders)
            if missing_repo_tracked:
                errors.append(
                    {
                        "field": "registry.artifact_boundary.repo_tracked_placeholders",
                        "message": f"registry artifact_boundary.repo_tracked_placeholders is missing: {', '.join(missing_repo_tracked)}",
                    }
                )
            reserved_runtime_roots = {
                str(item or "").strip()
                for item in list(artifact_boundary.get("reserved_runtime_roots") or [])
                if str(item or "").strip()
            }
            required_reserved_runtime_roots = {
                f"{project_root_rel}/_artifacts/proofs",
                f"{project_root_rel}/_artifacts/reports",
            }
            missing_reserved_runtime_roots = sorted(
                path for path in required_reserved_runtime_roots if path not in reserved_runtime_roots
            )
            if missing_reserved_runtime_roots:
                errors.append(
                    {
                        "field": "registry.artifact_boundary.reserved_runtime_roots",
                        "message": f"registry artifact_boundary.reserved_runtime_roots is missing: {', '.join(missing_reserved_runtime_roots)}",
                    }
                )
        compatibility_metadata = (
            record.get("compatibility_metadata") if isinstance(record.get("compatibility_metadata"), dict) else {}
        )
        if not compatibility_metadata:
            errors.append({"field": "registry.compatibility_metadata", "message": "registry compatibility_metadata is required"})
        else:
            if str(compatibility_metadata.get("schema_version") or "") != REGISTRY_COMPATIBILITY_SCHEMA_VERSION:
                errors.append(
                    {
                        "field": "registry.compatibility_metadata.schema_version",
                        "message": f"registry compatibility_metadata.schema_version must be {REGISTRY_COMPATIBILITY_SCHEMA_VERSION}",
                    }
                )
            if str(compatibility_metadata.get("scaffold_version") or "") != str(record.get("scaffold_version") or ""):
                errors.append(
                    {
                        "field": "registry.compatibility_metadata.scaffold_version",
                        "message": "registry compatibility_metadata.scaffold_version must match registry scaffold_version",
                    }
                )
            if str(compatibility_metadata.get("discovery_status") or "") != str(record.get("discovery_status") or ""):
                errors.append(
                    {
                        "field": "registry.compatibility_metadata.discovery_status",
                        "message": "registry compatibility_metadata.discovery_status must match registry discovery_status",
                    }
                )
            if str(compatibility_metadata.get("upgrade_readiness") or "") != "unknown":
                errors.append(
                    {
                        "field": "registry.compatibility_metadata.upgrade_readiness",
                        "message": "registry compatibility metadata must default to upgrade_readiness=unknown until explicit upgrade tooling exists",
                    }
                )
            anchors = compatibility_metadata.get("contract_anchors") if isinstance(compatibility_metadata.get("contract_anchors"), dict) else {}
            required_anchors = {
                "canonical_contract_path": expected_contract_path,
                "scaffold_manifest_path": expected_manifest_path,
                "layout_contract_path": LAYOUT_CONTRACT_PATH,
                "run_lifecycle_contract_path": RUN_LIFECYCLE_CONTRACT_PATH,
            }
            for field_name, expected_value in sorted(required_anchors.items()):
                if str(anchors.get(field_name) or "") != expected_value:
                    errors.append(
                        {
                            "field": f"registry.compatibility_metadata.contract_anchors.{field_name}",
                            "message": f"registry compatibility_metadata.contract_anchors.{field_name} must be {expected_value}",
                        }
                    )

    generated_rows = list(manifest.get("generated_files") or []) if manifest else []
    placeholder_rows = list(manifest.get("placeholder_files") or []) if manifest else []
    reserved_rows = list(manifest.get("reserved_paths") or []) if manifest else []
    ownership_by_path = {
        str(row.get("path") or ""): str(row.get("ownership") or "")
        for row in generated_rows
        if isinstance(row, dict) and str(row.get("path") or "")
    }
    purposes_by_path = {
        str(row.get("path") or ""): str(row.get("purpose") or "")
        for row in generated_rows
        if isinstance(row, dict) and str(row.get("path") or "")
    }
    placeholder_paths = {
        str(row.get("path") or ""): row
        for row in placeholder_rows
        if isinstance(row, dict) and str(row.get("path") or "")
    }

    if manifest:
        if str(manifest.get("layout_contract") or "") != LAYOUT_CONTRACT_PATH:
            errors.append({"field": "manifest.layout_contract", "message": f"manifest layout_contract must be {LAYOUT_CONTRACT_PATH}"})
        version_contract = manifest.get("version_contract") if isinstance(manifest.get("version_contract"), dict) else {}
        if str(version_contract.get("schema_version") or "") != SCAFFOLD_VERSION_CONTRACT_SCHEMA_VERSION:
            errors.append({"field": "manifest.version_contract", "message": f"manifest version_contract.schema_version must be {SCAFFOLD_VERSION_CONTRACT_SCHEMA_VERSION}"})
        if str(version_contract.get("current_version") or "") != SCAFFOLD_VERSION:
            errors.append({"field": "manifest.version_contract.current_version", "message": f"manifest version_contract.current_version must be {SCAFFOLD_VERSION}"})
        if version_contract.get("overwrite_generated_requires_exact_version") is not True:
            errors.append({"field": "manifest.version_contract.overwrite_generated_requires_exact_version", "message": "manifest version_contract must require exact-version overwrite_generated refreshes"})
        if str(manifest.get("generated_by") or "") != GENERATOR_ENTRYPOINT:
            errors.append({"field": "manifest.generated_by", "message": f"manifest generated_by must be {GENERATOR_ENTRYPOINT}"})
        if str(manifest.get("discovery_registry_path") or "") != REGISTRY_RELATIVE_PATH:
            errors.append({"field": "manifest.discovery_registry_path", "message": f"manifest discovery_registry_path must be {REGISTRY_RELATIVE_PATH}"})
        if str(manifest.get("project_id") or "") != project_key:
            errors.append({"field": "manifest.project_id", "message": f"manifest project_id must be {project_key}"})
        if str(manifest.get("project_root") or "") != project_root_rel:
            errors.append({"field": "manifest.project_root", "message": f"manifest project_root must be {project_root_rel}"})
        for path, ownership in sorted(ownership_by_path.items()):
            if ownership not in FILE_OWNERSHIP_SET:
                errors.append({"field": f"manifest.generated_files[{path}]", "message": f"unsupported ownership marker: {ownership}"})
        expected_placeholder_paths = {
            str(row.get("path") or "")
            for row in generated_rows
            if isinstance(row, dict) and str(row.get("kind") or "") in PLACEHOLDER_FILE_KINDS
        }
        actual_placeholder_paths = set(placeholder_paths)
        missing_placeholder_paths = sorted(path for path in expected_placeholder_paths if path not in actual_placeholder_paths)
        unexpected_placeholder_paths = sorted(path for path in actual_placeholder_paths if path not in expected_placeholder_paths)
        if missing_placeholder_paths:
            errors.append({"field": "manifest.placeholder_files", "message": f"placeholder_files is missing required paths: {', '.join(missing_placeholder_paths)}"})
        if unexpected_placeholder_paths:
            errors.append({"field": "manifest.placeholder_files", "message": f"placeholder_files contains non-placeholder paths: {', '.join(unexpected_placeholder_paths)}"})
        for path, row in sorted(placeholder_paths.items()):
            if path not in ownership_by_path:
                errors.append({"field": f"manifest.placeholder_files[{path}]", "message": "placeholder_files path must also exist in generated_files"})
                continue
            if str(row.get("ownership") or "") != ownership_by_path.get(path):
                errors.append({"field": f"manifest.placeholder_files[{path}].ownership", "message": "placeholder_files ownership must match generated_files ownership"})
            if str(row.get("completion_mode") or "") != "replace_or_fill_before_real_runs":
                errors.append({"field": f"manifest.placeholder_files[{path}].completion_mode", "message": "placeholder_files entries must use replace_or_fill_before_real_runs completion_mode"})
        if ownership_by_path.get(expected_contract_path) != "operator_owned":
            errors.append({"field": "manifest.generated_files.contract", "message": "canonical experiment contract must be operator_owned in the scaffold manifest"})
        if ownership_by_path.get(expected_manifest_path) != "generator_owned":
            errors.append({"field": "manifest.generated_files.scaffold_manifest", "message": "scaffold manifest must remain generator_owned"})
        if expected_project_runner_manifest in ownership_by_path and "compatibility-only" not in purposes_by_path.get(expected_project_runner_manifest, ""):
            errors.append({"field": "manifest.generated_files.project_runner_manifest", "message": "project_runner_manifest stub must remain explicitly compatibility-only"})
        if ownership_by_path.get(expected_data_root_policy) != "generator_owned":
            errors.append({"field": "manifest.generated_files.data_root_policy", "message": "artifact boundary policy file must remain generator_owned metadata"})
        lifecycle_generator_owned = {
            expected_lifecycle_readme,
            expected_run_record_placeholder,
            expected_interventions_placeholder,
            expected_resume_placeholder,
            expected_checkpoint_placeholder,
        }
        for lifecycle_path in sorted(lifecycle_generator_owned):
            if ownership_by_path.get(lifecycle_path) != "generator_owned":
                errors.append({"field": f"manifest.generated_files[{lifecycle_path}]", "message": "lifecycle placeholder pack files must remain generator_owned"})
        reserved_paths = {str(row.get("path") or "") for row in reserved_rows if isinstance(row, dict)}
        overlap = sorted(path for path in reserved_paths if path in ownership_by_path)
        if overlap:
            errors.append({"field": "manifest.reserved_paths", "message": f"reserved runtime paths must not also appear in generated_files: {', '.join(overlap)}"})
        lifecycle_seams = [row for row in list(manifest.get("lifecycle_seams") or []) if isinstance(row, dict)]
        lifecycle_seams_by_id = {str(row.get("seam_id") or ""): row for row in lifecycle_seams if str(row.get("seam_id") or "")}
        required_lifecycle_seams = {
            "run_record": ("run_record.json", expected_run_record_placeholder),
            "interventions": ("run_record.interventions.jsonl", expected_interventions_placeholder),
            "checkpoint_resume": ("resume_from.json", expected_resume_placeholder),
            "checkpoint_bundle_root": ("checkpoint_bundles_v2", expected_checkpoint_placeholder),
        }
        for seam_id, (runtime_filename, placeholder_path) in sorted(required_lifecycle_seams.items()):
            seam = lifecycle_seams_by_id.get(seam_id)
            if not seam:
                errors.append({"field": "manifest.lifecycle_seams", "message": f"missing required lifecycle seam: {seam_id}"})
                continue
            if str(seam.get("runtime_filename") or "") != runtime_filename:
                errors.append({"field": f"manifest.lifecycle_seams[{seam_id}].runtime_filename", "message": f"lifecycle seam {seam_id} must target {runtime_filename}"})
            if str(seam.get("placeholder_path") or "") != placeholder_path:
                errors.append({"field": f"manifest.lifecycle_seams[{seam_id}].placeholder_path", "message": f"lifecycle seam {seam_id} must point at {placeholder_path}"})
            if str(seam.get("source_of_truth_contract") or "") != RUN_LIFECYCLE_CONTRACT_PATH:
                errors.append({"field": f"manifest.lifecycle_seams[{seam_id}].source_of_truth_contract", "message": f"lifecycle seam {seam_id} must point at {RUN_LIFECYCLE_CONTRACT_PATH}"})
        rule_ids = {str(row.get("rule_id") or "") for row in list(manifest.get("placeholder_completion_rules") or []) if isinstance(row, dict)}
        required_rule_ids = {
            "launch_placeholder",
            "registry_placeholder",
            "resource_placeholder",
            "analysis_placeholder",
            "context_bundle_confirmation",
            "artifact_boundary_confirmation",
            "profile_placeholder",
            "lifecycle_run_record_placeholder",
            "lifecycle_checkpoint_placeholder",
            "lifecycle_intervention_placeholder",
        }
        missing_rule_ids = sorted(rule_id for rule_id in required_rule_ids if rule_id not in rule_ids)
        if missing_rule_ids:
            errors.append({"field": "manifest.placeholder_completion_rules", "message": f"missing required placeholder completion rules: {', '.join(missing_rule_ids)}"})

    if contract:
        summary = contract_result.get("summary") if isinstance(contract_result.get("summary"), dict) else {}
        if str(summary.get("project_id") or "") != project_key:
            errors.append({"field": "contract.summary.project_id", "message": f"contract summary project_id must be {project_key}"})
        limitations = [str(item or "") for item in list(contract.get("limitations") or [])]
        if not any("Scaffold-generated contract is not launch-ready" in item for item in limitations):
            errors.append({"field": "contract.limitations", "message": "contract limitations must keep the scaffold placeholder launch boundary explicit"})
        context_policy = contract.get("context_policy") if isinstance(contract.get("context_policy"), dict) else {}
        if str(context_policy.get("required_context_bundle") or "") != str(record.get("required_context_bundle") or summary.get("required_context_bundle") or ""):
            errors.append({"field": "registry.required_context_bundle", "message": "registry required_context_bundle must match the canonical contract summary"})
        resources = contract.get("resources") if isinstance(contract.get("resources"), dict) else {}
        if str(resources.get("resource_class") or "") != str(record.get("resource_class") or summary.get("resource_class") or ""):
            errors.append({"field": "registry.resource_class", "message": "registry resource_class must match the canonical contract summary"})
        analysis = contract.get("analysis") if isinstance(contract.get("analysis"), dict) else {}
        if str(analysis.get("default_recipe") or "") != str(record.get("analysis_recipe") or ""):
            errors.append({"field": "registry.analysis_recipe", "message": "registry analysis_recipe must match the contract default recipe"})
        artifacts = contract.get("artifacts") if isinstance(contract.get("artifacts"), dict) else {}
        if str(artifacts.get("external_data_root") or "") != str(record.get("external_data_root") or ""):
            errors.append({"field": "registry.external_data_root", "message": "registry external_data_root must match the canonical contract"})

    project_root = experiment_project_root(project_key, repo_root=root)
    tracked_missing = []
    for rel_path in sorted(ownership_by_path):
        if not (root / rel_path).resolve().exists():
            tracked_missing.append(rel_path)
    if tracked_missing:
        errors.append({"field": "generated_files", "message": f"generated scaffold paths are missing on disk: {', '.join(tracked_missing)}"})

    ok = not errors
    return {
        "ok": ok,
        "project_id": project_key,
        "project_root": _display_path(project_root, repo_root=root),
        "seam_contract_path": SEAM_CONTRACT_PATH,
        "migration_notes_path": MIGRATION_NOTES_PATH,
        "registry_path": REGISTRY_RELATIVE_PATH,
        "contract_path": expected_contract_path,
        "manifest_path": expected_manifest_path,
        "semantic_truth": {
            "canonical_contract": expected_contract_path,
            "structural_layout_manifest": expected_manifest_path,
            "discovery_registry": REGISTRY_RELATIVE_PATH,
            "compatibility_only_surface": expected_project_runner_manifest if expected_project_runner_manifest in ownership_by_path else "",
            "lifecycle_placeholder_pack": expected_lifecycle_readme,
        },
        "ownership_summary": {
            "generator_owned": sorted(path for path, ownership in ownership_by_path.items() if ownership == "generator_owned"),
            "operator_owned": sorted(path for path, ownership in ownership_by_path.items() if ownership == "operator_owned"),
            "downstream_runtime_owned": sorted(path for path, ownership in ownership_by_path.items() if ownership == "downstream_runtime_owned"),
        },
        "placeholder_rule_ids": sorted({str(row.get("rule_id") or "") for row in list(manifest.get("placeholder_completion_rules") or []) if isinstance(row, dict)}),
        "errors": errors,
        "warnings": warnings,
    }


def build_experiment_bootstrap_walkthrough(
    project_id: str,
    *,
    display_name: str = "",
    owner: str = "",
    tags: Sequence[str] | None = None,
    family_tags: Sequence[str] | None = None,
    default_profile: str = "starter",
    analysis_recipe: str = "summary_report",
    required_context_bundle: str = "lab_core_v1",
    resource_class: str = "cpu_light",
    external_data_root: str = "",
    include_manifest_compatibility: bool = True,
    collision_policy: str = "fail",
    create: bool = False,
    dry_run: bool = False,
    repo_root: Optional[Path] = None,
) -> Dict[str, Any]:
    root = _repo_root(repo_root)
    if str(collision_policy or "").strip() not in SUPPORTED_COLLISION_POLICIES:
        raise ValueError(f"collision_policy must be one of: {', '.join(SUPPORTED_COLLISION_POLICIES)}")

    project_key = str(project_id or "").strip().lower()
    steps: List[Dict[str, Any]] = []
    placeholder_rules: List[Dict[str, Any]] = []

    if create or dry_run:
        scaffold_result = write_experiment_scaffold(
            project_key,
            display_name=display_name,
            owner=owner,
            tags=tags,
            family_tags=family_tags,
            default_profile=default_profile,
            analysis_recipe=analysis_recipe,
            required_context_bundle=required_context_bundle,
            resource_class=resource_class,
            external_data_root=external_data_root,
            include_manifest_compatibility=include_manifest_compatibility,
            collision_policy=collision_policy,
            dry_run=dry_run,
            repo_root=root,
        )
        projection = scaffold_result.get("projection") if isinstance(scaffold_result.get("projection"), dict) else {}
        placeholder_rules = [dict(row) for row in list(projection.get("placeholder_completion_rules") or []) if isinstance(row, dict)]
        steps.append(
            {
                "step_id": "scaffold_generation",
                "action": "create_experiment_scaffold" if create else "plan_experiment_scaffold",
                "ok": bool(scaffold_result.get("ok")),
                "status": str(scaffold_result.get("status") or ""),
                "project_root": str(scaffold_result.get("project_root") or ""),
                "written_paths": list(scaffold_result.get("written_paths") or []),
                "planned_paths": list(scaffold_result.get("planned_paths") or []),
                "collision_policy": str(scaffold_result.get("collision_policy") or collision_policy),
            }
        )
        if dry_run or not scaffold_result.get("ok"):
            return {
                "ok": bool(scaffold_result.get("ok")),
                "project_id": project_key,
                "mode": "dry_run" if dry_run else "create",
                "walkthrough_doc_path": BOOTSTRAP_WALKTHROUGH_DOC_PATH,
                "migration_notes_path": MIGRATION_NOTES_PATH,
                "steps": steps,
                "placeholder_completion_rules": placeholder_rules,
                "next_actions": [
                    "Create the scaffold without --dry-run to materialize files and registry entry.",
                    "Run the seam verifier once the scaffold exists on disk.",
                ] if dry_run else ["Resolve the reported collision or scaffold error before continuing."],
            }

    registry_result = resolve_registered_experiment(project_key, repo_root=root, verify_paths=True)
    steps.append(
        {
            "step_id": "discovery_resolution",
            "action": "resolve_registered_experiment",
            "ok": bool(registry_result.get("ok")),
            "registry_path": str(registry_result.get("registry_path") or REGISTRY_RELATIVE_PATH),
            "record": dict(registry_result.get("record") or {}),
        }
    )

    contract_result = read_experiment_contract(project_key, repo_root=root)
    steps.append(
        {
            "step_id": "canonical_contract_read",
            "action": "read_experiment_contract",
            "ok": bool(contract_result.get("ok")),
            "summary": dict(contract_result.get("summary") or {}),
            "prelaunch": dict(contract_result.get("prelaunch") or {}),
        }
    )

    manifest_read_result = read_experiment_scaffold_manifest(project_key, repo_root=root)
    seam_result = verify_experiment_contract_scaffold_seams(project_key, repo_root=root)
    steps.append(
        {
            "step_id": "ownership_boundary_verification",
            "action": "verify_experiment_contract_scaffold_seams",
            "ok": bool(seam_result.get("ok")),
            "errors": list(seam_result.get("errors") or []),
            "warnings": list(seam_result.get("warnings") or []),
            "semantic_truth": dict(seam_result.get("semantic_truth") or {}),
        }
    )

    summary = contract_result.get("summary") if isinstance(contract_result.get("summary"), dict) else {}
    if not placeholder_rules:
        manifest_payload = manifest_read_result.get("manifest") if isinstance(manifest_read_result.get("manifest"), dict) else {}
        placeholder_rules = [dict(row) for row in list(manifest_payload.get("placeholder_completion_rules") or []) if isinstance(row, dict)]
    next_actions = [
        "Replace the placeholder runner before scheduling non-dry runs.",
        "Fill the starter registry/profile placeholders and tighten resource declarations.",
        f"Review deferred risks in {MIGRATION_NOTES_PATH} before broad adoption.",
    ]
    ok = all(bool(step.get("ok")) for step in steps)
    return {
        "ok": ok,
        "project_id": project_key,
        "mode": "create" if create else "existing",
        "walkthrough_doc_path": BOOTSTRAP_WALKTHROUGH_DOC_PATH,
        "migration_notes_path": MIGRATION_NOTES_PATH,
        "steps": steps,
        "contract_summary": dict(summary),
        "placeholder_completion_rules": placeholder_rules,
        "next_actions": next_actions,
    }
