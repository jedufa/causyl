"""Deterministic experiment scaffold planning and creation helpers.

This module owns the layout/template contract for scaffolded experiments only.
It does not register experiments as the source of semantic truth, settle
run-lifecycle truth, or broaden contract semantics outside the canonical
experiment contract surface.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from .experiment_contract import compute_experiment_contract_hash
from .experiment_contract import validate_experiment_contract
from .experiment_registry import REGISTRY_RELATIVE_PATH
from .experiment_registry import build_registered_experiment_record
from .experiment_registry import experiment_registry_path
from .experiment_registry import plan_experiment_registration
from .experiment_registry import write_experiment_registry
from .experiment_scaffold_projection import DEFAULT_ANALYSIS_RECIPE
from .experiment_scaffold_projection import DEFAULT_CONTEXT_BUNDLE
from .experiment_scaffold_projection import DEFAULT_PROFILE_ID
from .experiment_scaffold_projection import DEFAULT_RESOURCE_CLASS
from .experiment_scaffold_projection import build_scaffold_projection


SCAFFOLD_MANIFEST_SCHEMA_VERSION = "experiment_scaffold_manifest_v1"
SCAFFOLD_VERSION = "experiment_scaffold_v2"
SCAFFOLD_VERSION_CONTRACT_SCHEMA_VERSION = "experiment_scaffold_version_contract_v1"
ARTIFACT_BOUNDARY_SCHEMA_VERSION = "experiment_artifact_boundary_policy_v1"
LAYOUT_CONTRACT_PATH = "docs/20_contracts/experiments/Contract_Experiment_Scaffold_Layout_V1.html"
RUN_LIFECYCLE_CONTRACT_PATH = "docs/20_contracts/experiments/Contract_Experiment_Run_Lifecycle_Truth_V1.html"
GENERATOR_ENTRYPOINT = "tools/bin/create_experiment_scaffold.py"
SUPPORTED_COLLISION_POLICIES = ("fail", "overwrite_generated")
FILE_OWNERSHIP_SET = frozenset({"generator_owned", "operator_owned", "downstream_runtime_owned"})
PLACEHOLDER_FILE_KINDS = frozenset(
    {
        "runner_stub",
        "experiment_contract_stub",
        "project_runner_manifest_stub",
        "profile_stub",
        "registry_stub",
        "analysis_stub",
        "analysis_recipe_stub",
        "lifecycle_run_record_placeholder",
        "lifecycle_interventions_placeholder",
        "lifecycle_resume_placeholder",
        "lifecycle_checkpoint_placeholder_notes",
    }
)


def _repo_root(repo_root: Optional[Path] = None) -> Path:
    if repo_root is not None:
        return Path(repo_root).resolve()
    return Path(__file__).resolve().parents[2]


def experiment_project_root(project_id: str, *, repo_root: Optional[Path] = None) -> Path:
    root = _repo_root(repo_root)
    return (root / "experiments" / "projects" / str(project_id or "").strip().lower()).resolve()


def experiment_scaffold_manifest_path(project_id: str, *, repo_root: Optional[Path] = None) -> Path:
    return (experiment_project_root(project_id, repo_root=repo_root) / "_scaffold" / "scaffold_manifest.json").resolve()


def _json_dump(payload: Mapping[str, Any]) -> str:
    return json.dumps(payload, indent=2, sort_keys=True) + "\n"


def _write_text(target: Path, content: str) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(str(content), encoding="utf-8")


def _markdown_header(*, ownership: str, project_id: str) -> str:
    return (
        f"<!-- generated_by: {GENERATOR_ENTRYPOINT}; scaffold_version: {SCAFFOLD_VERSION}; "
        f"ownership: {ownership}; project_id: {project_id} -->\n"
    )


def _python_header(*, ownership: str, project_id: str) -> str:
    return (
        "#!/usr/bin/env python3\n"
        f"# generated_by: {GENERATOR_ENTRYPOINT}\n"
        f"# scaffold_version: {SCAFFOLD_VERSION}\n"
        f"# ownership: {ownership}\n"
        f"# project_id: {project_id}\n"
    )


def _yaml_header(*, ownership: str, project_id: str) -> str:
    return (
        f"# generated_by: {GENERATOR_ENTRYPOINT}\n"
        f"# scaffold_version: {SCAFFOLD_VERSION}\n"
        f"# ownership: {ownership}\n"
        f"# project_id: {project_id}\n"
    )


def _contract_stub(*, projection: Mapping[str, Any]) -> Dict[str, Any]:
    project_id = str(projection["project_id"])
    project_root = str(projection["project_root_relative"])
    display_name = str(projection["display_name"])
    owner = str(projection["owner"])
    tags = list(projection["tags"])
    default_profile = str(projection["default_profile"])
    analysis_recipe = str(projection["analysis_recipe"])
    contract: Dict[str, Any] = {
        "schema_version": "experiment_contract_v1",
        "contract_hash": "",
        "compatibility": {
            "contract_family": "experiment_contract",
            "major": 1,
            "minor": 0,
        },
        "project": {
            "project_id": project_id,
            "display_name": display_name,
            "owner": owner,
            "tags": tags,
        },
        "launch": {
            "default_runner_id": "default",
            "supported_runners": ["default"],
            "entrypoints": {
                "default": {
                    "command": ["python3", "-u", f"{project_root}/_runner/run_experiment.py"],
                    "working_dir": project_root,
                    "env": {"PYTHONUNBUFFERED": "1"},
                    "required_args": ["registry"],
                    "optional_args": ["dry_run", "profile"],
                }
            },
            "args": {
                "required": ["registry"],
                "optional": ["dry_run", "profile"],
                "types": {
                    "registry": "path",
                    "profile": "string",
                    "dry_run": "bool",
                },
            },
        },
        "configuration": {
            "profiles_root": f"{project_root}/_config",
            "registry_root": f"{project_root}/_spec/registry/v1",
            "override_modes": ["profile"],
            "default_profile": default_profile,
        },
        "observability": {
            "status_files": [
                {"path": "_artifacts/proofs/scaffold_stub_status.json", "type": "json", "priority": 1},
            ],
            "progress": {"mode": "none", "fields": []},
            "health_signals": ["exit_code"],
        },
        "control": {
            "pause_supported": False,
            "resume_supported": False,
            "stop_supported": True,
            "restart_supported": False,
            "checkpoint_supported": False,
            "branch_from_checkpoint_supported": False,
            "intervention_actions": ["graceful_stop"],
            "unsupported_actions": [
                "branch_from_checkpoint",
                "checkpoint",
                "pause",
                "restart",
                "resume",
            ],
        },
        "analysis": {
            "primary_artifacts": [f"analysis/{analysis_recipe}.md", "_artifacts/proofs/scaffold_stub_status.json"],
            "recipes": [analysis_recipe],
            "default_recipe": analysis_recipe,
        },
        "resources": {
            "resource_class": str(projection["resource_class"]),
            "project_semaphore": str(projection["project_semaphore"]),
            "max_parallel_runs": 1,
            "estimated_footprint": {
                "gpu_count": 0,
                "cpu_cores": 1,
                "ram_gb": 1,
            },
        },
        "limitations": [
            "Scaffold-generated contract is not launch-ready until the operator replaces the placeholder runner and fills real registry/config semantics.",
            "Generated contract values are starter defaults and must not be mistaken for domain policy or runtime guarantees.",
        ],
        "artifacts": {
            "repo_tracked_outputs": [
                "_artifacts/README.md",
                "_artifacts/data_root_policy.json",
                f"analysis/{analysis_recipe}.md",
                "small operator-readable notes",
            ],
            "external_data_root": str(projection["external_data_root"]),
            "artifact_manifest_required": False,
        },
        "context_policy": {
            "required_context_bundle": str(projection["required_context_bundle"]),
            "additional_context_refs_allowed": False,
            "forbidden_context_refs": ["full_repo_dump", "legacy_transcript_blob"],
        },
        "validation": {
            "schema_validation_required": True,
            "hash_validation_required": True,
            "prelaunch_checks": ["profiles_root_exists", "registry_root_exists"],
        },
    }
    contract["contract_hash"] = compute_experiment_contract_hash(contract)
    validation = validate_experiment_contract(contract)
    if not validation["ok"]:
        reasons = "; ".join(str(row.get("message") or "") for row in list(validation.get("errors") or []))
        raise ValueError(f"scaffold contract template became invalid: {reasons}")
    return contract


def _manifest_stub(*, projection: Mapping[str, Any]) -> Dict[str, Any]:
    project_id = str(projection["project_id"])
    project_root = str(projection["project_root_relative"])
    default_profile = str(projection["default_profile"])
    return {
        "schema_version": "project_runner_manifest_v1",
        "project_id": project_id,
        "project_root": project_root,
        "command_prefix": ["python3", "-u"],
        "runners": {
            "default": {
                "entrypoint": f"{project_root}/_runner/run_experiment.py",
                "cwd": project_root,
                "description": "Compatibility-only scaffold runner manifest. The experiment contract remains canonical.",
                "args": {
                    "registry": {"type": "path", "required": True},
                    "profile": {"type": "string", "default": default_profile},
                    "dry_run": {"type": "bool", "default": False},
                },
            }
        },
    }


def _profile_stub(*, projection: Mapping[str, Any]) -> Dict[str, Any]:
    return {
        "profile_id": str(projection["default_profile"]),
        "project_id": str(projection["project_id"]),
        "description": "Replace placeholder values before enabling real experiment runs.",
        "completion_required": list(projection["completion_required"]),
    }


def _artifact_boundary_policy(*, projection: Mapping[str, Any]) -> Dict[str, Any]:
    project_id = str(projection["project_id"])
    project_root = str(projection["project_root_relative"])
    analysis_recipe = str(projection["analysis_recipe"])
    return {
        "schema_version": ARTIFACT_BOUNDARY_SCHEMA_VERSION,
        "project_id": project_id,
        "repo_tracked_placeholders": [
            f"{project_root}/_artifacts/README.md",
            f"{project_root}/_artifacts/data_root_policy.json",
            f"{project_root}/analysis/{analysis_recipe}.md",
        ],
        "heavy_runtime_directories_created": [],
        "reserved_runtime_roots": [
            f"{project_root}/_artifacts/proofs",
            f"{project_root}/_artifacts/reports",
        ],
        "external_data_root": str(projection["external_data_root"]),
        "policy": "Heavy or mutable runtime artifacts must stay outside the tracked repo tree.",
    }


def _version_contract_payload() -> Dict[str, Any]:
    return {
        "schema_version": SCAFFOLD_VERSION_CONTRACT_SCHEMA_VERSION,
        "family": "experiment_scaffold",
        "current_version": SCAFFOLD_VERSION,
        "overwrite_generated_requires_exact_version": True,
        "upgrade_policy": "explicit_migration_required_for_older_or_unknown_versions",
        "compatibility_notes": [
            "Scaffold manifests from older or unknown versions must not be silently refreshed with overwrite_generated.",
            "Lifecycle-aware placeholder seams are structural markers only; canonical run truth still belongs to the experiment run lifecycle contract.",
        ],
    }


def _lifecycle_seams_payload(*, project_root: str) -> List[Dict[str, str]]:
    lifecycle_root = f"{project_root}/_lifecycle"
    return [
        {
            "seam_id": "run_record",
            "placeholder_path": f"{lifecycle_root}/run_record.placeholder.json",
            "runtime_filename": "run_record.json",
            "status": "reserved_placeholder",
            "source_of_truth_contract": RUN_LIFECYCLE_CONTRACT_PATH,
            "note": "Per-run canonical run records land later in wrapper directories; this scaffold file is a naming/ownership marker only.",
        },
        {
            "seam_id": "interventions",
            "placeholder_path": f"{lifecycle_root}/run_record.interventions.placeholder.jsonl",
            "runtime_filename": "run_record.interventions.jsonl",
            "status": "reserved_placeholder",
            "source_of_truth_contract": RUN_LIFECYCLE_CONTRACT_PATH,
            "note": "Intervention history stays append-only when later lifecycle adoption replaces this placeholder pack.",
        },
        {
            "seam_id": "checkpoint_resume",
            "placeholder_path": f"{lifecycle_root}/resume_from.placeholder.json",
            "runtime_filename": "resume_from.json",
            "status": "reserved_placeholder",
            "source_of_truth_contract": RUN_LIFECYCLE_CONTRACT_PATH,
            "note": "Checkpoint lineage and resume pointers remain deferred to the lifecycle-truth family; the scaffold only reserves the seam.",
        },
        {
            "seam_id": "checkpoint_bundle_root",
            "placeholder_path": f"{lifecycle_root}/checkpoint_bundles_v2/README.md",
            "runtime_filename": "checkpoint_bundles_v2",
            "status": "reserved_placeholder",
            "source_of_truth_contract": RUN_LIFECYCLE_CONTRACT_PATH,
            "note": "The scaffold reserves the checkpoint bundle root name without generating live checkpoint payloads.",
        },
    ]


def _scaffold_manifest_payload(*, projection: Mapping[str, Any], files: Sequence[Dict[str, str]]) -> Dict[str, Any]:
    project_id = str(projection["project_id"])
    display_name = str(projection["display_name"])
    project_root = str(projection["project_root_relative"])
    placeholder_files = [
        {
            "path": row["path"],
            "kind": row["kind"],
            "ownership": row["ownership"],
            "completion_mode": "replace_or_fill_before_real_runs",
        }
        for row in files
        if str(row.get("kind") or "") in PLACEHOLDER_FILE_KINDS
    ]
    return {
        "schema_version": SCAFFOLD_MANIFEST_SCHEMA_VERSION,
        "scaffold_version": SCAFFOLD_VERSION,
        "project_id": project_id,
        "display_name": display_name,
        "project_root": project_root,
        "layout_contract": LAYOUT_CONTRACT_PATH,
        "version_contract": _version_contract_payload(),
        "generated_by": GENERATOR_ENTRYPOINT,
        "projection_summary": dict(projection["projection_summary"]),
        "placeholder_completion_rules": list(projection["placeholder_completion_rules"]),
        "placeholder_files": placeholder_files,
        "lifecycle_seams": _lifecycle_seams_payload(project_root=project_root),
        "discovery_registry_path": REGISTRY_RELATIVE_PATH,
        "collision_policy": {
            "default": "fail",
            "supported": list(SUPPORTED_COLLISION_POLICIES),
        },
        "generated_files": [
            {
                "path": row["path"],
                "kind": row["kind"],
                "ownership": row["ownership"],
                "purpose": row["purpose"],
            }
            for row in files
        ],
        "reserved_paths": [
            {
                "path": f"{project_root}/_artifacts/proofs",
                "ownership": "downstream_runtime_owned",
                "purpose": "runtime proofs and placeholder-run status outputs",
            },
            {
                "path": f"{project_root}/_artifacts/reports",
                "ownership": "downstream_runtime_owned",
                "purpose": "operator-readable reports generated by later experiment workflows",
            },
            {
                "path": str(projection["external_data_root"]),
                "ownership": "downstream_runtime_owned",
                "purpose": "external heavy-data root reserved outside the git tree",
            },
        ],
    }


def _readme_text(*, project_id: str, display_name: str, projection: Mapping[str, Any]) -> str:
    note_projection = dict(projection.get("note_projection") or {})
    default_profile = str(note_projection.get("default_profile") or "starter")
    return (
        _markdown_header(ownership="operator_owned", project_id=project_id)
        + f"# {display_name}\n\n"
        + "This directory was created by the deterministic experiment scaffold generator.\n\n"
        + "## What exists already\n\n"
        + "- canonical starter contract under `_runner/experiment_contract.json`\n"
        + "- compatibility runner manifest stub under `_runner/project_runner_manifest.json`\n"
        + "- starter profile/config and registry skeletons\n"
        + "- operator notes, analysis placeholders, and artifact boundary notes\n\n"
        + "## What still needs human completion\n\n"
        + "1. Replace `_runner/run_experiment.py` with a real experiment entrypoint.\n"
        + f"2. Fill `_spec/registry/v1/{default_profile}.yaml` with domain-specific run definitions.\n"
        + "3. Tighten `_runner/experiment_contract.json` so resource, control, and analysis declarations are truthful.\n"
    )


def _runner_readme_text(*, project_id: str) -> str:
    return (
        _markdown_header(ownership="generator_owned", project_id=project_id)
        + "# Runner Surface\n\n"
        + "Generator-owned structural notes live here. The contract and starter runner file are scaffolded, but they remain operator-owned completion points.\n"
    )


def _config_readme_text(*, project_id: str) -> str:
    return (
        _markdown_header(ownership="generator_owned", project_id=project_id)
        + "# Config Surface\n\n"
        + "Keep lightweight tracked profiles here. Heavy data belongs under the external data root declared in the contract, not in git.\n"
    )


def _spec_readme_text(*, project_id: str) -> str:
    return (
        _markdown_header(ownership="generator_owned", project_id=project_id)
        + "# Spec Surface\n\n"
        + "Registry and spec files live under `_spec/registry/v1/`. The scaffold only creates a minimal starter placeholder.\n"
    )


def _artifacts_readme_text(*, project_id: str) -> str:
    return (
        _markdown_header(ownership="generator_owned", project_id=project_id)
        + "# Artifact Boundary\n\n"
        + "Use `_artifacts/` only for small tracked notes or proofs. Runtime-heavy outputs belong in the external data root declared by the contract.\n"
    )


def _lifecycle_readme_text(*, project_id: str) -> str:
    return (
        _markdown_header(ownership="generator_owned", project_id=project_id)
        + "# Lifecycle Placeholder Pack\n\n"
        + "This directory reserves canonical lifecycle-truth filenames without pretending that the scaffold already owns live run state.\n\n"
        + "Use it as a contract seam only:\n\n"
        + "- `run_record.placeholder.json` marks the future `run_record.json` surface\n"
        + "- `run_record.interventions.placeholder.jsonl` marks the future intervention log surface\n"
        + "- `resume_from.placeholder.json` marks the future checkpoint resume pointer surface\n"
        + "- `checkpoint_bundles_v2/README.md` reserves the future checkpoint bundle root name\n"
    )


def _lifecycle_placeholder_json(*, seam_id: str, runtime_filename: str, note: str) -> str:
    return _json_dump(
        {
            "schema_version": "experiment_scaffold_lifecycle_placeholder_v1",
            "scaffold_version": SCAFFOLD_VERSION,
            "seam_id": seam_id,
            "runtime_filename": runtime_filename,
            "source_of_truth_contract": RUN_LIFECYCLE_CONTRACT_PATH,
            "status": "reserved_placeholder",
            "note": note,
        }
    )


def _lifecycle_interventions_placeholder_text() -> str:
    return json.dumps(
        {
            "schema_version": "experiment_scaffold_lifecycle_placeholder_v1",
            "scaffold_version": SCAFFOLD_VERSION,
            "seam_id": "interventions",
            "runtime_filename": "run_record.interventions.jsonl",
            "source_of_truth_contract": RUN_LIFECYCLE_CONTRACT_PATH,
            "status": "reserved_placeholder",
            "note": "Append-only intervention history belongs to the lifecycle-truth family once real run wrappers exist.",
        },
        sort_keys=True,
    ) + "\n"


def _checkpoint_placeholder_readme_text(*, project_id: str) -> str:
    return (
        _markdown_header(ownership="generator_owned", project_id=project_id)
        + "# Checkpoint Placeholder Root\n\n"
        + "This tracked README reserves the `checkpoint_bundles_v2/` path so later lifecycle adoption can materialize checkpoint bundles without changing the scaffold shape.\n"
    )


def _operator_notes_text(*, project_id: str, projection: Mapping[str, Any]) -> str:
    note_projection = dict(projection.get("note_projection") or {})
    default_profile = str(note_projection.get("default_profile") or "starter")
    required_context_bundle = str(note_projection.get("required_context_bundle") or DEFAULT_CONTEXT_BUNDLE)
    resource_class = str(note_projection.get("resource_class") or DEFAULT_RESOURCE_CLASS)
    project_semaphore = str(note_projection.get("project_semaphore") or f"cpu/{project_id}")
    max_parallel_runs = int(note_projection.get("max_parallel_runs") or 1)
    external_data_root = str(note_projection.get("external_data_root") or f"~/causyl_experiment_data/{project_id}")
    return (
        _markdown_header(ownership="operator_owned", project_id=project_id)
        + "# Operator Notes\n\n"
        + "## Starter constraints\n\n"
        + f"- resource class defaults to `{resource_class}` until you declare a real footprint\n"
        + f"- project semaphore defaults to `{project_semaphore}`\n"
        + f"- max parallel runs defaults to `{max_parallel_runs}`\n"
        + f"- required context bundle defaults to `{required_context_bundle}`\n"
        + f"- external data root placeholder: `{external_data_root}`\n"
        + f"- starter profile placeholder id: `{default_profile}`\n\n"
        + "## Completion checklist\n\n"
        + "- replace placeholder runner behavior\n"
        + f"- fill registry/spec entries for `{default_profile}`\n"
        + "- verify the contract before scheduling any real runs\n"
    )


def _analysis_readme_text(*, project_id: str) -> str:
    return (
        _markdown_header(ownership="operator_owned", project_id=project_id)
        + "# Analysis Stub Pack\n\n"
        + "This scaffold reserves one analysis surface without pretending to know the science.\n\n"
        + "Expected future work:\n\n"
        + "- define real analysis recipes\n"
        + "- map primary artifacts honestly\n"
        + "- keep outputs under `_artifacts/` or the external data root\n"
    )


def _analysis_recipe_text(*, project_id: str, analysis_recipe: str) -> str:
    return (
        _markdown_header(ownership="operator_owned", project_id=project_id)
        + f"# {analysis_recipe}\n\n"
        + "Replace this scaffold placeholder with a real analysis recipe once the experiment semantics are known.\n"
    )


def _runner_script_text(*, project_id: str, display_name: str) -> str:
    return (
        _python_header(ownership="operator_owned", project_id=project_id)
        + '"""Scaffold placeholder runner for the generated experiment.\n\n'
        + "Replace this file before enabling real experiment launches.\n"
        + '"""\n\n'
        + "from __future__ import annotations\n\n"
        + "import argparse\n"
        + "import json\n"
        + "from pathlib import Path\n\n"
        + "def main() -> int:\n"
        + "    parser = argparse.ArgumentParser(description=\"Scaffold placeholder runner\")\n"
        + "    parser.add_argument(\"--registry\", required=True)\n"
        + "    parser.add_argument(\"--profile\", default=\"starter\")\n"
        + "    parser.add_argument(\"--dry_run\", action=\"store_true\")\n"
        + "    args = parser.parse_args()\n"
        + "    status = {\n"
        + f"        \"project_id\": \"{project_id}\",\n"
        + f"        \"display_name\": \"{display_name}\",\n"
        + "        \"status\": \"scaffold_incomplete\",\n"
        + "        \"registry\": args.registry,\n"
        + "        \"profile\": args.profile,\n"
        + "        \"dry_run\": bool(args.dry_run),\n"
        + "        \"message\": \"Replace _runner/run_experiment.py and tighten the experiment contract before scheduling real runs.\",\n"
        + "    }\n"
        + "    status_path = Path(\"_artifacts/proofs/scaffold_stub_status.json\")\n"
        + "    status_path.parent.mkdir(parents=True, exist_ok=True)\n"
        + "    status_path.write_text(json.dumps(status, indent=2, sort_keys=True) + \"\\n\", encoding=\"utf-8\")\n"
        + "    print(json.dumps(status, sort_keys=True))\n"
        + "    return 2\n\n"
        + "if __name__ == \"__main__\":\n"
        + "    raise SystemExit(main())\n"
    )


def _starter_registry_text(*, project_id: str, display_name: str) -> str:
    return (
        _yaml_header(ownership="operator_owned", project_id=project_id)
        + f"project_id: {project_id}\n"
        + "registry_id: starter\n"
        + f"display_name: {display_name} Starter Registry\n"
        + "status: placeholder\n"
        + "notes:\n"
        + "  - Replace placeholder fields before using this registry for real experiment runs.\n"
        + "  - Keep heavy runtime outputs outside the git tree.\n"
    )


def _file_record(*, path: str, content: str, kind: str, ownership: str, purpose: str) -> Dict[str, str]:
    if ownership not in FILE_OWNERSHIP_SET:
        raise ValueError(f"unsupported ownership: {ownership}")
    return {
        "path": path,
        "content": content,
        "kind": kind,
        "ownership": ownership,
        "purpose": purpose,
    }


def build_experiment_scaffold_plan(
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
    include_manifest_compatibility: bool = True,
    repo_root: Optional[Path] = None,
) -> Dict[str, Any]:
    root = _repo_root(repo_root)
    projection = build_scaffold_projection(
        project_id,
        display_name=display_name,
        owner=owner,
        tags=tags,
        family_tags=family_tags,
        default_profile=default_profile,
        analysis_recipe=analysis_recipe,
        required_context_bundle=required_context_bundle,
        resource_class=resource_class,
        external_data_root=external_data_root,
        repo_root=root,
    )
    project_key = str(projection["project_id"])
    display = str(projection["display_name"])
    owner_text = str(projection["owner"])
    tag_list = list(projection["tags"])
    project_root_rel = str(projection["project_root_relative"])
    project_root = (root / project_root_rel).resolve()

    contract = _contract_stub(projection=projection)
    files: List[Dict[str, str]] = [
        _file_record(
            path=f"{project_root_rel}/README.md",
            content=_readme_text(project_id=project_key, display_name=display, projection=projection),
            kind="project_readme",
            ownership="operator_owned",
            purpose="human-facing starter summary and completion checklist",
        ),
        _file_record(
            path=f"{project_root_rel}/_runner/README.md",
            content=_runner_readme_text(project_id=project_key),
            kind="runner_notes",
            ownership="generator_owned",
            purpose="generator-owned notes for the runner surface",
        ),
        _file_record(
            path=f"{project_root_rel}/_runner/run_experiment.py",
            content=_runner_script_text(project_id=project_key, display_name=display),
            kind="runner_stub",
            ownership="operator_owned",
            purpose="placeholder runner that fails honestly until replaced",
        ),
        _file_record(
            path=f"{project_root_rel}/_runner/experiment_contract.json",
            content=_json_dump(contract),
            kind="experiment_contract_stub",
            ownership="operator_owned",
            purpose="canonical experiment contract starter stub",
        ),
        _file_record(
            path=f"{project_root_rel}/_config/README.md",
            content=_config_readme_text(project_id=project_key),
            kind="config_notes",
            ownership="generator_owned",
            purpose="generator-owned notes for tracked config/profile files",
        ),
        _file_record(
            path=f"{project_root_rel}/_config/profiles/{projection['default_profile']}.json",
            content=_json_dump(_profile_stub(projection=projection)),
            kind="profile_stub",
            ownership="operator_owned",
            purpose="starter profile skeleton with explicit completion notes",
        ),
        _file_record(
            path=f"{project_root_rel}/_spec/README.md",
            content=_spec_readme_text(project_id=project_key),
            kind="spec_notes",
            ownership="generator_owned",
            purpose="generator-owned notes for the spec/registry surface",
        ),
        _file_record(
            path=f"{project_root_rel}/_spec/registry/v1/{projection['default_profile']}.yaml",
            content=_starter_registry_text(project_id=project_key, display_name=display),
            kind="registry_stub",
            ownership="operator_owned",
            purpose="starter registry/spec placeholder",
        ),
        _file_record(
            path=f"{project_root_rel}/_docs/operator_notes.md",
            content=_operator_notes_text(project_id=project_key, projection=projection),
            kind="operator_notes",
            ownership="operator_owned",
            purpose="short operator guidance and completion checklist",
        ),
        _file_record(
            path=f"{project_root_rel}/analysis/README.md",
            content=_analysis_readme_text(project_id=project_key),
            kind="analysis_stub",
            ownership="operator_owned",
            purpose="analysis placeholder pack aligned with the contract starter recipe",
        ),
        _file_record(
            path=f"{project_root_rel}/analysis/{projection['analysis_recipe']}.md",
            content=_analysis_recipe_text(project_id=project_key, analysis_recipe=str(projection["analysis_recipe"])),
            kind="analysis_recipe_stub",
            ownership="operator_owned",
            purpose="starter analysis recipe placeholder aligned with the contract default recipe",
        ),
        _file_record(
            path=f"{project_root_rel}/_artifacts/README.md",
            content=_artifacts_readme_text(project_id=project_key),
            kind="artifact_notes",
            ownership="generator_owned",
            purpose="artifact-boundary and external-data-root guidance",
        ),
        _file_record(
            path=f"{project_root_rel}/_artifacts/data_root_policy.json",
            content=_json_dump(_artifact_boundary_policy(projection=projection)),
            kind="artifact_boundary_policy",
            ownership="generator_owned",
            purpose="metadata-safe boundary file describing tracked placeholders and external runtime data root",
        ),
        _file_record(
            path=f"{project_root_rel}/_lifecycle/README.md",
            content=_lifecycle_readme_text(project_id=project_key),
            kind="lifecycle_placeholder_notes",
            ownership="generator_owned",
            purpose="generator-owned notes for reserved lifecycle-truth seam placeholders",
        ),
        _file_record(
            path=f"{project_root_rel}/_lifecycle/run_record.placeholder.json",
            content=_lifecycle_placeholder_json(
                seam_id="run_record",
                runtime_filename="run_record.json",
                note="Canonical per-run run_record.json files are introduced by the lifecycle-truth family, not by the base scaffold.",
            ),
            kind="lifecycle_run_record_placeholder",
            ownership="generator_owned",
            purpose="reserved placeholder for the canonical run_record.json seam",
        ),
        _file_record(
            path=f"{project_root_rel}/_lifecycle/run_record.interventions.placeholder.jsonl",
            content=_lifecycle_interventions_placeholder_text(),
            kind="lifecycle_interventions_placeholder",
            ownership="generator_owned",
            purpose="reserved placeholder for the append-only run_record.interventions.jsonl seam",
        ),
        _file_record(
            path=f"{project_root_rel}/_lifecycle/resume_from.placeholder.json",
            content=_lifecycle_placeholder_json(
                seam_id="checkpoint_resume",
                runtime_filename="resume_from.json",
                note="Checkpoint resume pointers stay deferred until the lifecycle-truth family binds real run wrappers and checkpoint lineage.",
            ),
            kind="lifecycle_resume_placeholder",
            ownership="generator_owned",
            purpose="reserved placeholder for the resume_from.json checkpoint seam",
        ),
        _file_record(
            path=f"{project_root_rel}/_lifecycle/checkpoint_bundles_v2/README.md",
            content=_checkpoint_placeholder_readme_text(project_id=project_key),
            kind="lifecycle_checkpoint_placeholder_notes",
            ownership="generator_owned",
            purpose="tracked placeholder root reserving checkpoint_bundles_v2 for later lifecycle adoption",
        ),
    ]
    if include_manifest_compatibility:
        files.insert(
            4,
            _file_record(
                path=f"{project_root_rel}/_runner/project_runner_manifest.json",
                content=_json_dump(_manifest_stub(projection=projection)),
                kind="project_runner_manifest_stub",
                ownership="operator_owned",
                purpose="compatibility-only runner manifest stub for current branch reality",
            ),
        )

    manifest_rel_path = f"{project_root_rel}/_scaffold/scaffold_manifest.json"
    manifest_row = _file_record(
        path=manifest_rel_path,
        content="",
        kind="scaffold_manifest",
        ownership="generator_owned",
        purpose="stable scaffold version marker and ownership manifest",
    )
    scaffold_manifest = _scaffold_manifest_payload(projection=projection, files=[*files, manifest_row])
    manifest_row["content"] = _json_dump(scaffold_manifest)
    files.append(manifest_row)

    directories = sorted({str(Path(row["path"]).parent) for row in files})
    return {
        "project_id": project_key,
        "display_name": display,
        "owner": owner_text,
        "tags": tag_list,
        "family_tags": list(projection["family_tags"]),
        "default_profile": str(projection["default_profile"]),
        "analysis_recipe": str(projection["analysis_recipe"]),
        "required_context_bundle": str(projection["required_context_bundle"]),
        "resource_class": str(projection["resource_class"]),
        "external_data_root": str(projection["external_data_root"]),
        "project_root": str(project_root),
        "project_root_relative": project_root_rel,
        "scaffold_version": SCAFFOLD_VERSION,
        "layout_contract": LAYOUT_CONTRACT_PATH,
        "projection": projection,
        "registry_path": REGISTRY_RELATIVE_PATH,
        "include_manifest_compatibility": bool(include_manifest_compatibility),
        "supported_collision_policies": list(SUPPORTED_COLLISION_POLICIES),
        "directories": directories,
        "files": files,
        "scaffold_manifest_path": str(project_root / "_scaffold" / "scaffold_manifest.json"),
        "contract_path": str(project_root / "_runner" / "experiment_contract.json"),
    }


def _load_existing_manifest(path: Path) -> Dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if str(data.get("schema_version") or "").strip() != SCAFFOLD_MANIFEST_SCHEMA_VERSION:
        raise ValueError("existing scaffold manifest has unsupported schema_version")
    if str(data.get("scaffold_version") or "").strip() != SCAFFOLD_VERSION:
        raise ValueError("existing scaffold manifest has unsupported scaffold_version")
    files = data.get("generated_files")
    if not isinstance(files, list):
        raise ValueError("existing scaffold manifest is missing generated_files")
    return data


def _existing_project_root_state(project_root: Path) -> Tuple[bool, List[str]]:
    if not project_root.exists():
        return False, []
    entries = sorted(path.name for path in project_root.iterdir())
    return True, entries


def _plan_actions(plan: Mapping[str, Any], *, collision_policy: str, repo_root: Optional[Path] = None) -> Tuple[List[Dict[str, str]], List[Dict[str, str]]]:
    policy = str(collision_policy or "").strip()
    if policy not in SUPPORTED_COLLISION_POLICIES:
        raise ValueError(f"collision_policy must be one of: {', '.join(SUPPORTED_COLLISION_POLICIES)}")
    root = _repo_root(repo_root)
    project_root = (root / str(plan["project_root_relative"])).resolve()
    manifest_path = project_root / "_scaffold" / "scaffold_manifest.json"
    root_exists, root_entries = _existing_project_root_state(project_root)
    collisions: List[Dict[str, str]] = []
    actions: List[Dict[str, str]] = []

    if policy == "fail":
        if root_exists and root_entries:
            collisions.append(
                {
                    "type": "project_root_not_empty",
                    "path": str(project_root),
                    "message": "project root already exists and is not empty; rerun with --collision-policy overwrite_generated only after verifying ownership markers",
                }
            )
        for row in list(plan["files"]):
            abs_path = (root / row["path"]).resolve()
            if abs_path.exists():
                collisions.append(
                    {
                        "type": "file_exists",
                        "path": row["path"],
                        "message": f"planned scaffold file already exists: {row['path']}",
                    }
                )
            else:
                actions.append({"action": "write", "path": row["path"]})
        return actions, collisions

    if root_exists and not manifest_path.exists():
        collisions.append(
            {
                "type": "partial_scaffold_without_manifest",
                "path": str(project_root),
                "message": "project root exists but no scaffold manifest is present; refuse overwrite_generated to avoid clobbering untracked operator work",
            }
        )
        return [], collisions

    existing_manifest: Dict[str, Any] = {}
    if manifest_path.exists():
        try:
            existing_manifest = _load_existing_manifest(manifest_path)
        except Exception as exc:
            collisions.append(
                {
                    "type": "invalid_existing_manifest",
                    "path": str(manifest_path),
                    "message": f"cannot use overwrite_generated because the existing scaffold manifest is invalid: {exc}",
                }
            )
            return [], collisions
    ownership_by_path = {
        str(row.get("path") or ""): str(row.get("ownership") or "")
        for row in list(existing_manifest.get("generated_files") or [])
        if str(row.get("path") or "")
    }
    for row in list(plan["files"]):
        rel_path = str(row["path"])
        abs_path = (root / rel_path).resolve()
        ownership = str(row["ownership"])
        if not abs_path.exists():
            actions.append({"action": "write", "path": rel_path})
            continue
        existing_ownership = ownership_by_path.get(rel_path, ownership)
        if rel_path == str(Path(plan["project_root_relative"]) / "_scaffold" / "scaffold_manifest.json"):
            actions.append({"action": "overwrite", "path": rel_path})
            continue
        if existing_ownership == "generator_owned":
            actions.append({"action": "overwrite", "path": rel_path})
            continue
        if ownership == "operator_owned":
            actions.append({"action": "skip_existing", "path": rel_path})
            continue
        collisions.append(
            {
                "type": "existing_unhandled_path",
                "path": rel_path,
                "message": f"existing path could not be classified for overwrite_generated: {rel_path}",
            }
        )
    return actions, collisions


def write_experiment_scaffold(
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
    include_manifest_compatibility: bool = True,
    collision_policy: str = "fail",
    dry_run: bool = False,
    repo_root: Optional[Path] = None,
) -> Dict[str, Any]:
    plan = build_experiment_scaffold_plan(
        project_id,
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
        repo_root=repo_root,
    )
    root = _repo_root(repo_root)
    actions, collisions = _plan_actions(plan, collision_policy=collision_policy, repo_root=root)
    registration_record = build_registered_experiment_record(
        project_id=str(plan["project_id"]),
        display_name=str(plan["display_name"]),
        project_root=str(plan["project_root_relative"]),
        contract_path=f"{plan['project_root_relative']}/_runner/experiment_contract.json",
        scaffold_manifest_path=f"{plan['project_root_relative']}/_scaffold/scaffold_manifest.json",
        scaffold_version=str(plan["scaffold_version"]),
        tags=list(plan["tags"]),
        family_tags=list(plan.get("family_tags") or []),
        required_context_bundle=str(plan["required_context_bundle"]),
        resource_class=str(plan["resource_class"]),
        analysis_recipe=str(plan["analysis_recipe"]),
        external_data_root=str(plan["external_data_root"]),
        layout_contract_path=LAYOUT_CONTRACT_PATH,
        run_lifecycle_contract_path=RUN_LIFECYCLE_CONTRACT_PATH,
    )
    registration = plan_experiment_registration(registration_record, repo_root=root)
    if not registration.get("ok"):
        collisions = list(collisions) + list(registration.get("collisions") or [])

    result: Dict[str, Any] = {
        "ok": not collisions,
        "status": "planned" if dry_run and not collisions else ("created" if not collisions else "collision"),
        "project_id": str(plan["project_id"]),
        "display_name": str(plan["display_name"]),
        "project_root": str(plan["project_root_relative"]),
        "scaffold_version": str(plan["scaffold_version"]),
        "layout_contract": str(plan["layout_contract"]),
        "projection": plan["projection"],
        "collision_policy": str(collision_policy),
        "dry_run": bool(dry_run),
        "planned_paths": [str(row["path"]) for row in list(plan["files"])],
        "actions": actions,
        "collisions": collisions,
        "written_paths": [],
        "skipped_paths": [row["path"] for row in actions if row["action"] == "skip_existing"],
        "registration": registration,
    }
    if dry_run or collisions:
        return result

    file_lookup = {str(row["path"]): row for row in list(plan["files"])}
    written: List[str] = []
    backups: List[Tuple[Path, Optional[bytes]]] = []
    project_root = (root / str(plan["project_root_relative"])).resolve()
    project_root_preexisted = project_root.exists()
    registry_path = experiment_registry_path(repo_root=root)
    registry_backup: Optional[bytes] = registry_path.read_bytes() if registry_path.exists() else None
    try:
        for action in list(actions):
            if action["action"] == "skip_existing":
                continue
            row = file_lookup[action["path"]]
            target = (root / row["path"]).resolve()
            target.parent.mkdir(parents=True, exist_ok=True)
            backups.append((target, target.read_bytes() if target.exists() else None))
            _write_text(target, str(row["content"]))
            written.append(str(row["path"]))
        payload = registration.get("payload") if isinstance(registration.get("payload"), dict) else None
        if payload is not None:
            write_experiment_registry(payload, repo_root=root)
    except Exception:
        for target, previous in reversed(backups):
            if previous is None:
                if target.exists():
                    target.unlink()
            else:
                target.write_bytes(previous)
        if registry_backup is None:
            if registry_path.exists():
                registry_path.unlink()
        else:
            registry_path.parent.mkdir(parents=True, exist_ok=True)
            registry_path.write_bytes(registry_backup)
        if not project_root_preexisted and project_root.exists():
            shutil.rmtree(project_root)
        raise

    result["written_paths"] = written
    result["status"] = "created"
    result["registration"] = {
        **registration,
        "status": "unchanged" if str(registration.get("action") or "") == "noop" else "registered",
        "written_path": REGISTRY_RELATIVE_PATH,
    }
    return result
