from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from tools.git.support import git_apply


def _init_git_repo(tmp_path: Path) -> None:
    subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Git Apply Test"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "operator@example.invalid"], cwd=tmp_path, check=True, capture_output=True)
    (tmp_path / "src").mkdir(parents=True, exist_ok=True)
    (tmp_path / "src" / "hello.txt").write_text("hello\n", encoding="utf-8")
    subprocess.run(["git", "add", "src/hello.txt"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "baseline"], cwd=tmp_path, check=True, capture_output=True)


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _run_git_apply(tmp_path: Path, diff_path: Path) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ)
    env["PYTHONPATH"] = str(_repo_root()) + os.pathsep + env.get("PYTHONPATH", "")
    return subprocess.run(
        [sys.executable, "-m", "tools.git.support.git_apply", "apply", "--yes", str(diff_path)],
        cwd=tmp_path,
        text=True,
        capture_output=True,
        env=env,
        check=False,
    )


def test_normalize_patch_for_repo_strips_repo_prefixes(tmp_path: Path) -> None:
    _init_git_repo(tmp_path)
    patch = """\
diff --git a/tmp/example/src/hello.txt b/tmp/example/src/hello.txt
--- a/tmp/example/src/hello.txt
+++ b/tmp/example/src/hello.txt
@@ -1 +1 @@
-hello
+patched
"""

    normalized = git_apply.normalize_patch_for_repo(tmp_path, patch)

    assert normalized.target_files == ["src/hello.txt"]
    assert "diff --git a/src/hello.txt b/src/hello.txt" in normalized.content
    assert "--- a/src/hello.txt" in normalized.content
    assert "+++ b/src/hello.txt" in normalized.content


def test_cli_applies_patch_after_normalizing_wrong_prefix(tmp_path: Path) -> None:
    _init_git_repo(tmp_path)
    diff_path = tmp_path / "gpt.patch"
    diff_path.write_text(
        """\
diff --git a/wrong/prefix/src/hello.txt b/wrong/prefix/src/hello.txt
--- a/wrong/prefix/src/hello.txt
+++ b/wrong/prefix/src/hello.txt
@@ -1 +1 @@
-hello
+patched
""",
        encoding="utf-8",
    )

    proc = _run_git_apply(tmp_path, diff_path)

    assert proc.returncode == 0, proc.stderr
    assert (tmp_path / "src" / "hello.txt").read_text(encoding="utf-8") == "patched"
    assert "Patch diagnosis: applied" in proc.stdout
    assert "patch was applied" in proc.stdout


def test_cli_returns_error_when_path_cannot_be_normalized(tmp_path: Path) -> None:
    _init_git_repo(tmp_path)
    diff_path = tmp_path / "bad.patch"
    diff_path.write_text(
        """\
diff --git a/no/match/here.txt b/no/match/here.txt
--- a/no/match/here.txt
+++ b/no/match/here.txt
@@ -1 +1 @@
-hello
+patched
""",
        encoding="utf-8",
    )

    proc = _run_git_apply(tmp_path, diff_path)

    assert proc.returncode != 0
    assert "Unable to normalize diff path" in proc.stderr


def test_check_mode_reports_blocked_files_concisely(tmp_path: Path) -> None:
    _init_git_repo(tmp_path)
    diff_path = tmp_path / "bad.patch"
    diff_path.write_text(
        """\
diff --git a/src/hello.txt b/src/hello.txt
--- a/src/hello.txt
+++ b/src/hello.txt
@@ -1 +1 @@
-not-hello
+patched
""",
        encoding="utf-8",
    )
    env = dict(os.environ)
    env["PYTHONPATH"] = str(_repo_root()) + os.pathsep + env.get("PYTHONPATH", "")
    proc = subprocess.run(
        [sys.executable, "-m", "tools.git.support.git_apply", "check", str(diff_path)],
        cwd=tmp_path,
        text=True,
        capture_output=True,
        env=env,
        check=False,
    )

    assert proc.returncode != 0
    assert "Patch diagnosis: failed" in proc.stdout
    assert "Blocked files:" in proc.stdout
    assert "src/hello.txt: file changed since patch was created" in proc.stdout
