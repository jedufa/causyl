from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
from pathlib import Path
from typing import Any, Dict


def repo_root() -> Path:
    return Path(__file__).resolve().parents[4]


def fixtures_root() -> Path:
    return Path(__file__).resolve().parents[1] / "fixtures"


def scenario_root(topic: str, name: str) -> Path:
    root = fixtures_root()
    direct = root / topic / name
    if direct.exists():
        return direct
    return root / "intent_sheet" / topic / name


def load_scenario(topic: str, name: str) -> Dict[str, Any]:
    root = scenario_root(topic, name)
    path = root / "scenario.json"
    scenario = json.loads(path.read_text(encoding="utf-8"))
    prompt_path = root / "prompt.md"
    scenario["prompt_text"] = prompt_path.read_text(encoding="utf-8") if prompt_path.exists() else ""
    scenario["scenario_root"] = str(root)
    return scenario


def _copy_tree_if_present(source: Path, target: Path) -> None:
    if source.exists():
        shutil.copytree(source, target, dirs_exist_ok=True)


def _init_git_repo(root: Path) -> None:
    subprocess.run(["git", "init"], cwd=str(root), check=True, capture_output=True, text=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=str(root), check=True, capture_output=True, text=True)
    subprocess.run(["git", "config", "user.name", "test"], cwd=str(root), check=True, capture_output=True, text=True)
    subprocess.run(["git", "add", "."], cwd=str(root), check=True, capture_output=True, text=True)
    subprocess.run(["git", "commit", "-m", "seed"], cwd=str(root), check=True, capture_output=True, text=True)


def bootstrap_runtime_root(runtime_root: Path, *, scenario: Dict[str, Any]) -> Path:
    source_root = repo_root()
    runtime_root.mkdir(parents=True, exist_ok=True)
    shutil.copytree(
        source_root / "governance" / "contracts",
        runtime_root / "governance" / "contracts",
        dirs_exist_ok=True,
    )
    (runtime_root / "runtime" / "state" / "e2e" / "action_plane").mkdir(parents=True, exist_ok=True)
    root = Path(str(scenario["scenario_root"]))
    _copy_tree_if_present(root / "seed", runtime_root)
    _copy_tree_if_present(root / "artifacts", runtime_root / "runtime" / "state" / "e2e" / "inputs" / scenario["name"])
    stage_artifact = str(scenario.get("stage_patch_in_cwd") or "").strip()
    if stage_artifact:
        source = root / stage_artifact
        if source.exists() and source.is_file():
            target = runtime_root / source.name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
    if bool(scenario.get("git_repo")):
        _init_git_repo(runtime_root)
    return runtime_root


def manifest_tree(root: Path) -> Dict[str, str]:
    manifest: Dict[str, str] = {}
    if not root.exists():
        return manifest
    for path in sorted(p for p in root.rglob("*") if p.is_file()):
        rel = path.relative_to(root).as_posix()
        manifest[rel] = hashlib.sha256(path.read_bytes()).hexdigest()
    return manifest
