"""Deterministic patch and refactor helper.

Last modified: 2026-03-30 23:57 Europe/Zurich
"""

from __future__ import annotations

import argparse
import difflib
import json
from pathlib import Path
from typing import Any, Sequence

from tools.deterministic_tooling.common import build_envelope, hash_text, normalized_list, read_json, read_text, repo_rel, write_output

SCHEMA_VERSION = "deterministic_patch_diff_v1"
TOOL_NAME = "deterministic_patch_refactor_helper"


def _apply_operation(text: str, operation: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    op = str(operation.get("op") or "").strip()
    if op == "replace_literal":
        old = str(operation.get("old") or "")
        new = str(operation.get("new") or "")
        count = text.count(old) if old else 0
        return text.replace(old, new), {"op": op, "count": count}
    if op == "append_text":
        addition = str(operation.get("text") or "")
        return text + addition, {"op": op, "count": 1 if addition else 0}
    if op == "prepend_text":
        addition = str(operation.get("text") or "")
        return addition + text, {"op": op, "count": 1 if addition else 0}
    if op == "insert_after_anchor":
        anchor = str(operation.get("anchor") or "")
        insertion = str(operation.get("text") or "")
        idx = text.find(anchor)
        if idx < 0:
            return text, {"op": op, "count": 0, "warning": "anchor_not_found"}
        insert_at = idx + len(anchor)
        return text[:insert_at] + insertion + text[insert_at:], {"op": op, "count": 1}
    return text, {"op": op or "unknown", "count": 0, "warning": "unsupported_operation"}


def build_patch_diff(*, repo_root: Path, recipe: Mapping[str, Any], target_files: Sequence[str]) -> dict[str, Any]:
    repo_root = Path(repo_root).resolve()
    warnings: list[dict[str, Any]] = []
    patch_chunks: list[str] = []
    recipe_log: list[dict[str, Any]] = []
    provenance: list[dict[str, Any]] = []
    operations = list(recipe.get("operations") or [])
    recipe_id = str(recipe.get("recipe_id") or "ad_hoc_recipe").strip()
    for rel in sorted(normalized_list(target_files)):
        path = (repo_root / rel).resolve()
        if not path.exists():
            warnings.append({"code": "missing_target_file", "severity": "warning", "message": f"Target file is missing: {rel}"})
            continue
        original = read_text(path)
        updated = original
        op_results: list[dict[str, Any]] = []
        for row in operations:
            if not isinstance(row, dict):
                continue
            updated, result = _apply_operation(updated, row)
            op_results.append(result)
            if result.get("warning"):
                warnings.append({"code": str(result["warning"]), "severity": "warning", "message": f"{rel}: {result['warning']}"})
        recipe_log.append({"path": rel, "operations": op_results})
        if updated == original:
            continue
        diff_lines = difflib.unified_diff(
            original.splitlines(),
            updated.splitlines(),
            fromfile=f"a/{rel}",
            tofile=f"b/{rel}",
            lineterm="",
        )
        patch_chunks.append("\n".join(diff_lines))
        provenance.append({"ref_type": "file", "ref": rel, "content_hash": hash_text(original)})

    patch_text = "\n\n".join(chunk for chunk in patch_chunks if chunk).strip()
    outputs = {
        "recipe_id": recipe_id,
        "patch_diff": patch_text,
        "patch_hash": hash_text(patch_text),
        "recipe_log": recipe_log,
        "canonical_replacements": [row for row in recipe_log if row.get("operations")],
    }
    return build_envelope(
        schema_version=SCHEMA_VERSION,
        tool_name=TOOL_NAME,
        inputs={"repo_root": str(repo_root), "recipe_id": recipe_id, "target_files": normalized_list(target_files)},
        outputs=outputs,
        warnings=warnings,
        provenance=provenance,
    )


def _load_recipe(value: str) -> dict[str, Any]:
    candidate = Path(value)
    if candidate.exists():
        return dict(read_json(candidate))
    return {"recipe_id": value, "operations": []}


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build a deterministic diff from a recipe")
    parser.add_argument("--repo-root", default=".")
    parser.add_argument("--recipe", required=True)
    parser.add_argument("--target-files", nargs="*", default=[])
    parser.add_argument("--output")
    args = parser.parse_args(argv)

    payload = build_patch_diff(repo_root=Path(args.repo_root), recipe=_load_recipe(args.recipe), target_files=args.target_files)
    write_output(Path(args.output).resolve() if args.output else None, payload)
    print(payload["outputs"]["patch_hash"])
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
