from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


def write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def main() -> int:
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        repo_root = root / "demo_repo"
        knowledge_root = root / "knowledge"
        output_root = root / "out"

        write(
            repo_root / "src" / "scheduler" / "job_runner.py",
            """
import airflow.providers

class JobRunner:
    def run_task(self):
        return True
""".strip()
            + "\n",
        )
        write(
            repo_root / "providers" / "email" / "provider.py",
            "class EmailProvider:\n    pass\n",
        )
        write(
            repo_root / "docs" / "README.md",
            "# Demo Repo\n",
        )
        write(
            knowledge_root / "terms.yaml",
            """
canonical_terms:
  scheduler:
    aliases: [orchestrator]
  provider:
    aliases: [plugin]
path_patterns:
  - "**/tests/**"
""".strip()
            + "\n",
        )

        cmd = [
            sys.executable,
            str(Path(__file__).resolve().parents[1] / "knowledge_miner.py"),
            "--repo-root",
            str(repo_root),
            "--knowledge-root",
            str(knowledge_root),
            "--output-root",
            str(output_root),
            "--progress-every",
            "1",
            "--min-term-frequency",
            "1",
            "--min-dir-frequency",
            "1",
            "--min-bucket-hits",
            "1",
        ]
        completed = subprocess.run(cmd, capture_output=True, text=True, check=False)
        print(completed.stdout)
        print(completed.stderr)
        if completed.returncode != 0:
            raise SystemExit(completed.returncode)

        summary = output_root / "summary.md"
        if not summary.exists():
            raise AssertionError("summary.md was not created")
        unknown_terms = json.loads((output_root / "reports" / "unknown_terms.json").read_text(encoding="utf-8"))
        proposal_counts = json.loads((output_root / "reports" / "proposal_counts.json").read_text(encoding="utf-8"))
        diff_dir = output_root / "diffs"
        patches = sorted(diff_dir.glob("*.patch"))

        if not unknown_terms:
            raise AssertionError("Expected unknown terms to be reported")
        if not patches:
            raise AssertionError("Expected at least one patch file")
        if proposal_counts["path_patterns"] < 1:
            raise AssertionError("Expected at least one path pattern proposal")

        print("Smoke test passed")
        print(f"Summary: {summary}")
        print(f"Patches: {[path.name for path in patches]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
