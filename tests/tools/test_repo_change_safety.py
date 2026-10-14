from __future__ import annotations

import os
import subprocess
import sys
import json
from pathlib import Path
import tempfile


def _init_git_repo(tmp_path: Path) -> None:
    subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Repo Safety Test"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "operator@example.invalid"], cwd=tmp_path, check=True, capture_output=True)
    # Baseline file to ensure repository is non-empty and has HEAD
    (tmp_path / "a.txt").write_text("base\n", encoding="utf-8")
    subprocess.run("git add a.txt", cwd=tmp_path, shell=True, check=True, capture_output=True)
    subprocess.run("git commit -m baseline", cwd=tmp_path, shell=True, check=True, capture_output=True)


def _repo_root() -> Path:
    # Repository root containing tools/patching/apply_patch.py
    return Path(__file__).resolve().parents[2]


def _make_patch_modify_file(tmp_path: Path, rel_path: str, new_content: str) -> Path:
    """Create a unified-diff patch that modifies an existing tracked file."""
    target = tmp_path / rel_path
    target.write_text(new_content, encoding="utf-8")
    subprocess.run(["git", "add", rel_path], cwd=tmp_path, check=True, capture_output=True)
    fd, temp_path = tempfile.mkstemp(prefix="repo_change_safety_", suffix=".diff")
    os.close(fd)
    patch_path = Path(temp_path)
    with patch_path.open("w", encoding="utf-8") as f2:
        subprocess.run(["git", "diff", "--cached"], cwd=tmp_path, check=True, stdout=f2)
    subprocess.run(["git", "reset", "--hard", "HEAD"], cwd=tmp_path, check=True, capture_output=True)
    return patch_path


def _make_patch_add_file(tmp_path: Path, rel_path: str, content: str) -> Path:
    """Create a unified-diff patch that adds a new file."""
    target = tmp_path / rel_path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    subprocess.run(["git", "add", rel_path], cwd=tmp_path, check=True, capture_output=True)
    fd, temp_path = tempfile.mkstemp(prefix="repo_change_safety_", suffix=".diff")
    os.close(fd)
    patch_path = Path(temp_path)
    with patch_path.open("w", encoding="utf-8") as f2:
        subprocess.run(["git", "diff", "--cached"], cwd=tmp_path, check=True, stdout=f2)
    subprocess.run(["git", "reset", "--hard", "HEAD"], cwd=tmp_path, check=True, capture_output=True)
    return patch_path


def _run_apply_patch(tmp_path: Path, args: list[str]) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env["PYTHONPATH"] = str(_repo_root()) + os.pathsep + env.get("PYTHONPATH", "")
    cmd = [sys.executable, "-m", "tools.patching.apply_patch", *args]
    return subprocess.run(cmd, cwd=tmp_path, env=env, text=True, capture_output=True)


def _read_patch_log(tmp_path: Path) -> list[dict]:
    log_path = tmp_path / "archives" / "patches" / "patch_log.jsonl"
    assert log_path.exists()
    return [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines() if line.strip()]


def test_strict_dirty_tree_blocks_apply(tmp_path: Path) -> None:
    _init_git_repo(tmp_path)
    # Create a valid patch that would add a new file if allowed
    patch_path = _make_patch_add_file(tmp_path, "b.txt", "hello-from-patch\n")
    # Make the worktree dirty (untracked file is sufficient)
    (tmp_path / "UNTRACKED.tmp").write_text("dirty\n", encoding="utf-8")

    proc = _run_apply_patch(tmp_path, ["--patch", str(patch_path), "--strict", "--yes"])
    assert proc.returncode != 0
    assert "Working tree is dirty (strict mode)." in proc.stderr


def test_overlap_nonmergeable_refuses_with_yes(tmp_path: Path) -> None:
    _init_git_repo(tmp_path)
    # Prepare a patch that modifies a.txt
    patch_path = _make_patch_modify_file(tmp_path, "a.txt", "patched\n")
    # Create a local uncommitted modification to the same file
    (tmp_path / "a.txt").write_text("local-edit\n", encoding="utf-8")

    proc = _run_apply_patch(tmp_path, ["--patch", str(patch_path), "--yes"])
    assert proc.returncode != 0
    # Accept either variant of the overlap message used by zip vs patch flows
    err = proc.stderr
    assert "overlaps locally modified files" in err or "cannot be auto-merged" in err


def test_clean_apply_succeeds(tmp_path: Path) -> None:
    _init_git_repo(tmp_path)
    patch_path = _make_patch_add_file(tmp_path, "b.txt", "clean-success\n")

    proc = _run_apply_patch(tmp_path, ["--patch", str(patch_path), "--strict", "--yes"])
    assert proc.returncode == 0
    assert (tmp_path / "b.txt").exists()
    assert (tmp_path / "b.txt").read_text(encoding="utf-8") == "clean-success\n"


def test_post_exec_failure_rolls_back_and_logs_failure_instead_of_applied(tmp_path: Path) -> None:
    _init_git_repo(tmp_path)
    patch_path = _make_patch_add_file(tmp_path, "b.txt", "rollback-me\n")

    proc = _run_apply_patch(
        tmp_path,
        ["--patch", str(patch_path), "--strict", "--yes", "--post-exec", "exit 9"],
    )

    assert proc.returncode == 6
    assert not (tmp_path / "b.txt").exists()
    entries = _read_patch_log(tmp_path)
    assert entries[-1]["result"] == "FAILED_POST_EXEC_ROLLED_BACK"
    assert entries[-1]["post_exec_returncode"] == 9
    assert entries[-1]["rollback_ok"] is True
    assert all(str(row.get("result")) != "APPLIED" for row in entries)
