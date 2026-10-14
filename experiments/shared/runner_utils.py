"""Shared runner utilities for experiment-project adapters."""

from __future__ import annotations

import os
from pathlib import Path
import sys
from typing import Dict, Mapping, Optional


def ensure_sys_path_prepend(path: Path) -> None:
    """Prepend an absolute path to ``sys.path`` if it is missing."""
    text = str(path.resolve())
    if text not in sys.path:
        sys.path.insert(0, text)


def project_root_rel(project_id: str) -> str:
    """Return the repo-relative project root for one experiment project."""

    project = str(project_id or "").strip().lower()
    if not project:
        raise ValueError("project_id is required")
    return f"experiments/projects/{project}"


def project_module_root(project_id: str) -> str:
    """Return the Python module root for one experiment project."""

    return project_root_rel(project_id).replace("/", ".")


def builder_harness_out_dir_rel(project_id: str) -> str:
    """Return the repo-relative generated harness directory for one project."""

    return f"{project_root_rel(project_id)}/experiments/harnesses/generated"


def builder_registry_out_dir_rel(project_id: str) -> str:
    """Return the repo-relative generated registry directory for one project."""

    return f"{project_root_rel(project_id)}/_spec/registry/v1/generated"


def builder_registry_root_rel(project_id: str) -> str:
    """Return the repo-relative canonical registry root for one project."""

    return f"{project_root_rel(project_id)}/_spec/registry"


def builder_harness_module_prefix(project_id: str) -> str:
    """Return the generated harness module prefix for one project."""

    return f"{project_module_root(project_id)}.experiments.harnesses.generated"


def builder_project_runner_module(project_id: str) -> str:
    """Return the project-local batch harness runner module."""

    return f"{project_module_root(project_id)}.experiments.harness.run_experiments"


def builder_run_job_module() -> str:
    """Return the shared wrapper runtime module used by the builder."""

    return "experiments_shared.run_job"


def builder_manifest_runner_module() -> str:
    """Return the shared manifest runtime module used by the builder."""

    return "experiments_shared.manifest_runner"


def resolve_registry_path(*, project_root: Path, raw: str, spec_subdir: str = "_spec/registry") -> Path:
    """Resolve a registry path against a project's canonical registry subtree."""
    text = str(raw or "").strip()
    if not text:
        raise ValueError("registry path is required")

    candidate = Path(text).expanduser()
    if candidate.is_absolute():
        if not candidate.is_file():
            raise FileNotFoundError(candidate)
        return candidate.resolve()

    spec_root = (project_root / spec_subdir).resolve()
    resolved = (spec_root / text).resolve()
    if resolved.is_file():
        return resolved
    raise FileNotFoundError(f"registry not found under {spec_subdir}: {text}")


def default_experiment_data_root() -> Path:
    """Return the shared heavy-data root for experiment projects."""
    raw = str(os.environ.get("CAUSYL_EXPERIMENT_DATA_ROOT") or "").strip()
    if raw:
        return Path(raw).expanduser().resolve()
    return (Path.home() / "causyl_experiment_data").expanduser().resolve()


def default_project_data_root(project_id: str) -> Path:
    """Return the heavy-data root for one project."""

    project = str(project_id or "").strip().lower()
    if not project:
        raise ValueError("project_id is required")
    return (default_experiment_data_root() / project).resolve()


def default_project_runs_root(project_id: str, *, external_data_root: Optional[Path] = None) -> Path:
    """Return the default durable runs root for one project."""

    base = external_data_root.resolve() if external_data_root is not None else default_project_data_root(project_id)
    return (base / "runs").resolve()


def _load_contract_external_data_root(
    *,
    project_id: str,
    repo_root: Path,
    env: Optional[Mapping[str, str]] = None,
) -> Optional[Path]:
    """Resolve a project contract's declared external data root when available."""

    from .experiment_artifact_boundary import resolve_external_data_root
    from .experiment_contract import read_experiment_contract

    result = read_experiment_contract(project_id, repo_root=repo_root)
    contract = result.get("contract") if isinstance(result.get("contract"), dict) else None
    if contract is None:
        return None
    artifacts = contract.get("artifacts") if isinstance(contract.get("artifacts"), dict) else {}
    declaration = str(artifacts.get("external_data_root") or "").strip()
    if not declaration:
        return None
    try:
        resolved = resolve_external_data_root(declaration, repo_root=repo_root, env=env)
    except ValueError:
        return None
    return Path(resolved["path"]).resolve()


def normalize_project_runs_root(
    *,
    repo_root: Path,
    project_root: Path,
    project_id: str,
    raw: str,
    external_data_root: Optional[Path] = None,
) -> Path:
    """Normalize a project runs root across absolute, repo-relative, and project-relative inputs."""

    text = str(raw or "").strip()
    if not text:
        return default_project_runs_root(project_id, external_data_root=external_data_root)

    candidate = Path(text).expanduser()
    if candidate.is_absolute():
        return candidate.resolve()

    repo_root = Path(repo_root).resolve()
    project_root = Path(project_root).resolve()
    if text.startswith("experiments/projects/"):
        return (repo_root / candidate).resolve()
    return (project_root / candidate).resolve()


def ensure_project_output_env(
    *,
    project_id: str,
    repo_root: Path,
    project_root: Path,
    runs_root: str = "",
    env: Optional[Mapping[str, str]] = None,
) -> Dict[str, str]:
    """Return normalized project output env vars for adapter entrypoints."""

    env_out = dict(os.environ)
    if env is not None:
        env_out.update({str(key): str(value) for key, value in dict(env).items()})

    repo_root = Path(repo_root).resolve()
    project_root = Path(project_root).resolve()
    resolved_data_root = _load_contract_external_data_root(project_id=project_id, repo_root=repo_root, env=env_out)
    if resolved_data_root is None:
        resolved_data_root = default_project_data_root(project_id)

    normalized_runs_root = normalize_project_runs_root(
        repo_root=repo_root,
        project_root=project_root,
        project_id=project_id,
        raw=runs_root,
        external_data_root=resolved_data_root,
    )
    if not str(env_out.get("EXPERIMENT_OUTPUTS_DIR") or "").strip():
        env_out["EXPERIMENT_OUTPUTS_DIR"] = str(resolved_data_root)
    return env_out
