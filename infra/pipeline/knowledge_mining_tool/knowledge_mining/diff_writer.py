from __future__ import annotations

import difflib
import logging
from pathlib import Path

from .models import DiffArtifact, ProposalFile, RunArtifacts
from .utils import ensure_dir


class DiffWriter:
    def __init__(self, *, logger: logging.Logger) -> None:
        self.logger = logger

    def write_diffs(
        self,
        *,
        knowledge_root: Path,
        proposal_files: list[ProposalFile],
        artifacts: RunArtifacts,
    ) -> list[DiffArtifact]:
        ensure_dir(artifacts.diffs_dir)
        diff_artifacts: list[DiffArtifact] = []
        aggregate_chunks: list[str] = []

        for index, proposal_file in enumerate(proposal_files, start=1):
            relpath = proposal_file.relpath
            actual_path = knowledge_root / relpath
            before = actual_path.read_text(encoding="utf-8") if actual_path.exists() else ""
            after = proposal_file.content
            diff_lines = list(
                difflib.unified_diff(
                    before.splitlines(),
                    after.splitlines(),
                    fromfile=f"a/{relpath.as_posix()}",
                    tofile=f"b/{relpath.as_posix()}",
                    lineterm="",
                )
            )
            if not diff_lines:
                self.logger.info("No diff generated for %s (content unchanged)", relpath)
                continue
            unified_diff = "\n".join(diff_lines) + "\n"
            patch_name = f"{index:03d}-{relpath.as_posix().replace('/', '-')}.patch"
            patch_path = artifacts.diffs_dir / patch_name
            patch_path.write_text(unified_diff, encoding="utf-8")
            diff_artifacts.append(
                DiffArtifact(
                    relpath=relpath,
                    patch_path=patch_path,
                    unified_diff=unified_diff,
                    proposal_count=proposal_file.proposal_count,
                )
            )
            aggregate_chunks.append(unified_diff)

        if aggregate_chunks:
            all_patch_path = artifacts.diffs_dir / "000-all.patch"
            all_patch_path.write_text("\n".join(aggregate_chunks), encoding="utf-8")
            self.logger.info("Wrote aggregate patch %s", all_patch_path)
        else:
            self.logger.info("No diff artifacts were generated.")

        return diff_artifacts
