from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Tuple

PIPELINE_KNOWLEDGE_FILE = "pipeline_knowledge.yaml"

DEFAULT_KNOWLEDGE_ASSET_DIR = "infra/pipeline/knowledge"
DEFAULT_KNOWLEDGE_ASSET_FILES: Dict[str, str] = {
    "task_families": "task_families.yaml",
    "task_family_lifecycle": "task_family_lifecycle.yaml",
    "task_profile_rules": "task_profile_rules.yaml",
    "failure_taxonomy": "failure_taxonomy.yaml",
    "failure_to_subsystem_map": "failure_to_subsystem_map.yaml",
    "invariant_templates": "invariant_templates.yaml",
    "derived_requirements": "derived_requirements.yaml",
    "cross_cutting_flags": "cross_cutting_flags.yaml",
    "subsystem_map": "subsystem_map.yaml",
    "subsystem_aliases": "subsystem_aliases.yaml",
    "conceptual_query_templates": "conceptual_query_templates.yaml",
    "lexical_clue_terms": "lexical_clue_terms.yaml",
    "domain_map": "domain_map.yaml",
    "retrieval_bias_rules": "retrieval_bias_rules.yaml",
    "retrieval_virtual_buckets": "retrieval_virtual_buckets.yaml",
    "role_assignment_rules": "role_assignment_rules.yaml",
    "negative_targets": "negative_targets.yaml",
    "responsibility_terms": "responsibility_terms.yaml",
    "passive_relevance_patterns": "passive_relevance_patterns.yaml",
    "recovery_signal_terms": "recovery_signal_terms.yaml",
    "bundle_coverage_rules": "bundle_coverage_rules.yaml",
    "scoring_weights": "scoring_weights.yaml",
    "patch_contract_templates": "patch_contract_templates.yaml",
}


def _coerce_mapping(value: Any) -> Dict[str, Any]:
    return dict(value or {}) if isinstance(value, Mapping) else {}


def _manifest_from_config_or_repo(repo_root: str | Path, config: Mapping[str, Any]) -> Dict[str, Any]:
    derived = _coerce_mapping(config.get("_derived"))
    manifest = _coerce_mapping(derived.get("knowledge_assets"))
    if manifest:
        return manifest
    return resolve_knowledge_asset_paths(repo_root, config)


def resolve_knowledge_asset_paths(repo_root: str | Path, config: Mapping[str, Any]) -> Dict[str, Any]:
    root = Path(repo_root).resolve()
    knowledge_cfg = _coerce_mapping(config.get("knowledge_assets"))
    files_cfg = _coerce_mapping(knowledge_cfg.get("files"))
    base_dir = str(knowledge_cfg.get("base_dir") or DEFAULT_KNOWLEDGE_ASSET_DIR)
    base_path = (root / base_dir).resolve()
    paths: Dict[str, str] = {}
    exists: Dict[str, bool] = {}
    for asset_name, default_name in DEFAULT_KNOWLEDGE_ASSET_FILES.items():
        raw_value = str(files_cfg.get(asset_name) or default_name).strip() or default_name
        candidate_path = Path(raw_value)
        if not candidate_path.is_absolute():
            if "/" in raw_value or "\\" in raw_value:
                candidate_path = (root / candidate_path).resolve()
            else:
                candidate_path = (base_path / raw_value).resolve()
        paths[asset_name] = str(candidate_path)
        exists[asset_name] = candidate_path.is_file()
    return {
        "base_dir": str(base_path),
        "files": paths,
        "exists": exists,
    }


def _strip_yaml_comment(text: str) -> str:
    out: List[str] = []
    in_single = False
    in_double = False
    for idx, ch in enumerate(text):
        if ch == "'" and not in_double:
            in_single = not in_single
        elif ch == '"' and not in_single:
            in_double = not in_double
        elif ch == "#" and not in_single and not in_double:
            if idx == 0 or text[idx - 1].isspace():
                break
        out.append(ch)
    return "".join(out).rstrip()


def _parse_yaml_scalar(raw: str) -> Any:
    text = _strip_yaml_comment(str(raw or "").strip())
    if text == "":
        return ""
    if text in {"[]", "{}"}:
        return [] if text == "[]" else {}
    lowered = text.lower()
    if lowered in {"true", "false"}:
        return lowered == "true"
    if lowered in {"null", "none", "~"}:
        return None
    if (text.startswith('"') and text.endswith('"')) or (text.startswith("'") and text.endswith("'")):
        return text[1:-1]
    if text.startswith("[") and text.endswith("]"):
        inner = text[1:-1].strip()
        if not inner:
            return []
        return [_parse_yaml_scalar(part.strip()) for part in inner.split(",")]
    if text.startswith("{") and text.endswith("}"):
        inner = text[1:-1].strip()
        if not inner:
            return {}
        out: Dict[str, Any] = {}
        for part in inner.split(","):
            if ":" not in part:
                raise ValueError(f"unsupported inline mapping entry: {part!r}")
            key, value = part.split(":", 1)
            out[str(_parse_yaml_scalar(key)).strip()] = _parse_yaml_scalar(value)
        return out
    if text.lstrip("-").isdigit():
        try:
            return int(text)
        except ValueError:
            pass
    if text.lstrip("-").replace(".", "", 1).isdigit():
        try:
            return float(text)
        except ValueError:
            pass
    return text


def _load_minimal_yaml(raw: str) -> Any:
    lines: List[Tuple[int, int, str]] = []
    for lineno, original in enumerate(raw.splitlines(), start=1):
        expanded = original.replace("\t", "    ")
        stripped = _strip_yaml_comment(expanded).strip()
        if not stripped:
            continue
        indent = len(expanded) - len(expanded.lstrip(" "))
        lines.append((lineno, indent, stripped))
    if not lines:
        return {}

    def consume_scalar_continuation(index: int, parent_indent: int, initial_value: str) -> Tuple[Any, int]:
        parts: List[str] = [_parse_yaml_scalar(initial_value)]
        while index < len(lines):
            _, next_indent, next_text = lines[index]
            if next_indent <= parent_indent:
                break
            if next_text.startswith("- "):
                break
            if ":" in next_text:
                break
            parts.append(_parse_yaml_scalar(next_text))
            index += 1
        if len(parts) == 1:
            return parts[0], index
        return " ".join(str(part).strip() for part in parts if str(part).strip()), index

    def parse_block(index: int, indent: int, kind: str) -> Tuple[Any, int]:
        if index < len(lines) and lines[index][1] == indent and lines[index][2] in {"[]", "{}"}:
            scalar = _parse_yaml_scalar(lines[index][2])
            return scalar, index + 1
        if kind == "dict":
            out: Dict[str, Any] = {}
            while index < len(lines):
                lineno, current_indent, text = lines[index]
                if current_indent < indent:
                    break
                if current_indent != indent:
                    raise ValueError(f"line {lineno}: unexpected indent for mapping entry")
                if text.startswith("-"):
                    break
                if ":" not in text:
                    raise ValueError(f"line {lineno}: unsupported yaml content")
                key, value = text.split(":", 1)
                key_txt = str(_parse_yaml_scalar(key)).strip()
                value_txt = value.strip()
                index += 1
                if value_txt:
                    scalar_value, index = consume_scalar_continuation(index, indent, value_txt)
                    out[key_txt] = scalar_value
                    continue
                if index >= len(lines) or lines[index][1] < indent:
                    out[key_txt] = {}
                    continue
                next_indent = lines[index][1]
                next_kind = "list" if lines[index][2].startswith("-") else "dict"
                child, index = parse_block(index, next_indent, next_kind)
                out[key_txt] = child
            return out, index

        out_list: List[Any] = []
        while index < len(lines):
            lineno, current_indent, text = lines[index]
            if current_indent < indent:
                break
            if current_indent != indent:
                raise ValueError(f"line {lineno}: unexpected indent for list entry")
            if not (text == "-" or text.startswith("- ")):
                break
            item_text = text[1:].strip()
            index += 1
            if not item_text:
                if index >= len(lines) or lines[index][1] <= indent:
                    out_list.append("")
                    continue
                child_kind = "list" if lines[index][2].startswith("-") else "dict"
                child, index = parse_block(index, lines[index][1], child_kind)
                out_list.append(child)
                continue
            if ":" in item_text and not item_text.startswith(("'", '"', "[", "{")):
                key, value = item_text.split(":", 1)
                item_map: Dict[str, Any] = {}
                key_txt = str(_parse_yaml_scalar(key)).strip()
                value_txt = value.strip()
                if value_txt:
                    item_map[key_txt] = _parse_yaml_scalar(value_txt)
                elif index < len(lines) and lines[index][1] > indent:
                    child_kind = "list" if lines[index][2].startswith("-") else "dict"
                    child, index = parse_block(index, lines[index][1], child_kind)
                    item_map[key_txt] = child
                else:
                    item_map[key_txt] = {}
                if index < len(lines) and lines[index][1] > indent and not lines[index][2].startswith("-"):
                    extra, index = parse_block(index, lines[index][1], "dict")
                    item_map.update(extra)
                out_list.append(item_map)
                continue
            scalar_value, index = consume_scalar_continuation(index, current_indent, item_text)
            out_list.append(scalar_value)
        return out_list, index

    root_kind = "list" if lines[0][2].startswith("-") else "dict"
    parsed, next_index = parse_block(0, lines[0][1], root_kind)
    if next_index != len(lines):
        lineno = lines[next_index][0]
        raise ValueError(f"line {lineno}: trailing yaml content could not be parsed")
    return parsed


def _load_asset_with_meta(path: Path) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    suffix = path.suffix.lower().lstrip(".")
    if not path.exists() or not path.is_file():
        return {}, {"path": str(path), "exists": False, "format": suffix, "loader": "missing", "parse_error": None}
    raw = path.read_text(encoding="utf-8")
    loader = "json"
    parse_error: Optional[str] = None
    if path.suffix.lower() == ".json":
        data: Any = json.loads(raw)
    else:
        loader = "yaml"
        data = None
        errors: List[str] = []
        try:
            import yaml  # type: ignore

            data = yaml.safe_load(raw) or {}
            loader = "pyyaml"
        except Exception as exc:
            errors.append(f"pyyaml: {exc}")
        if data is None:
            try:
                from ruamel.yaml import YAML  # type: ignore

                yaml_loader = YAML(typ="safe")
                data = yaml_loader.load(raw) or {}
                loader = "ruamel"
            except Exception as exc:
                errors.append(f"ruamel: {exc}")
        if data is None:
            try:
                data = _load_minimal_yaml(raw) or {}
                loader = "minimal_yaml"
            except Exception as exc:
                errors.append(f"minimal_yaml: {exc}")
                parse_error = "; ".join(errors)
                data = {}
    if not isinstance(data, Mapping):
        return {}, {
            "path": str(path),
            "exists": True,
            "format": suffix,
            "loader": loader,
            "parse_error": parse_error,
            "root_type": type(data).__name__,
        }
    return dict(data or {}), {"path": str(path), "exists": True, "format": suffix, "loader": loader, "parse_error": parse_error}


def _load_yaml_or_json_asset(path: Path) -> Dict[str, Any]:
    data, _ = _load_asset_with_meta(path)
    return data


def _asset_non_empty(value: Mapping[str, Any]) -> bool:
    if not isinstance(value, Mapping) or not value:
        return False
    for candidate_key in ("families", "flags", "patterns", "templates", "subsystems", "defaults"):
        candidate = value.get(candidate_key)
        if isinstance(candidate, Mapping) and candidate:
            return True
        if isinstance(candidate, list) and candidate:
            return True
    return any(bool(v) for v in value.values())


def _normalize_pipeline_knowledge_asset(asset_name: str, extracted: Any) -> Dict[str, Any]:
    if not isinstance(extracted, Mapping):
        return {}
    normalized = dict(extracted)
    if asset_name in {"task_families", "failure_taxonomy", "invariant_templates", "task_profile_rules", "derived_requirements"}:
        if "families" not in normalized:
            return {"families": normalized}
    return normalized


def _load_from_pipeline_knowledge_data(pipeline_asset: Mapping[str, Any], asset_name: str) -> Dict[str, Any]:
    extracted = pipeline_asset.get(asset_name)
    if extracted is None and asset_name == "derived_requirements":
        extracted = pipeline_asset.get("requirement_templates")
    return _normalize_pipeline_knowledge_asset(asset_name, extracted)


def _load_from_pipeline_knowledge(path: Path, asset_name: str) -> Dict[str, Any]:
    pipeline_asset, _ = _load_asset_with_meta(path)
    return _load_from_pipeline_knowledge_data(pipeline_asset, asset_name)


def load_knowledge_asset_with_meta(repo_root: str | Path, config: Mapping[str, Any], asset_name: str) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    manifest = _manifest_from_config_or_repo(repo_root, config)
    path = Path(str(manifest.get("files", {}).get(asset_name) or "")).resolve()
    asset, direct_meta = _load_asset_with_meta(path)
    if _asset_non_empty(asset):
        return asset, {**direct_meta, "source": "direct", "normalized": False}
    pipeline_path = Path(str(manifest.get("base_dir") or "")).resolve() / PIPELINE_KNOWLEDGE_FILE
    pipeline_asset, pipeline_meta = _load_asset_with_meta(pipeline_path)
    fallback = _load_from_pipeline_knowledge_data(pipeline_asset, asset_name)
    if _asset_non_empty(fallback):
        return fallback, {**pipeline_meta, "source": "pipeline_knowledge", "normalized": True}
    return asset, {
        **direct_meta,
        "source": "empty",
        "normalized": False,
        "pipeline_fallback_loader": pipeline_meta.get("loader"),
        "pipeline_fallback_error": pipeline_meta.get("parse_error"),
    }


def load_knowledge_asset(repo_root: str | Path, config: Mapping[str, Any], asset_name: str) -> Dict[str, Any]:
    asset, _ = load_knowledge_asset_with_meta(repo_root, config, asset_name)
    return asset


def load_knowledge_assets(
    repo_root: str | Path,
    config: Mapping[str, Any],
    asset_names: Optional[Iterable[str]] = None,
) -> Dict[str, Dict[str, Any]]:
    available = list(DEFAULT_KNOWLEDGE_ASSET_FILES.keys())
    requested = [str(name) for name in list(asset_names or available)]
    out: Dict[str, Dict[str, Any]] = {}
    for asset_name in requested:
        out[asset_name] = load_knowledge_asset(repo_root, config, asset_name)
    return out


def load_knowledge_assets_with_meta(
    repo_root: str | Path,
    config: Mapping[str, Any],
    asset_names: Optional[Iterable[str]] = None,
) -> Tuple[Dict[str, Dict[str, Any]], Dict[str, Dict[str, Any]]]:
    available = list(DEFAULT_KNOWLEDGE_ASSET_FILES.keys())
    requested = [str(name) for name in list(asset_names or available)]
    assets: Dict[str, Dict[str, Any]] = {}
    meta: Dict[str, Dict[str, Any]] = {}
    for asset_name in requested:
        loaded, loaded_meta = load_knowledge_asset_with_meta(repo_root, config, asset_name)
        assets[asset_name] = loaded
        meta[asset_name] = loaded_meta
    return assets, meta


def missing_required_knowledge_assets(repo_root: str | Path, config: Mapping[str, Any], asset_names: Iterable[str]) -> Dict[str, Dict[str, Any]]:
    _, meta = load_knowledge_assets_with_meta(repo_root, config, asset_names)
    return {asset_name: dict(asset_meta) for asset_name, asset_meta in meta.items() if asset_meta.get("source") == "empty"}


def describe_knowledge_assets(repo_root: str | Path, config: Mapping[str, Any], asset_names: Optional[Iterable[str]] = None) -> Dict[str, Any]:
    manifest = _manifest_from_config_or_repo(repo_root, config)
    requested = [str(name) for name in list(asset_names or DEFAULT_KNOWLEDGE_ASSET_FILES.keys())]
    base_path = Path(str(manifest.get("base_dir") or "")).resolve()
    pipeline_path = base_path / PIPELINE_KNOWLEDGE_FILE
    diagnostics: Dict[str, Any] = {
        "base_dir": str(base_path),
        "pipeline_knowledge_exists": pipeline_path.is_file(),
        "assets": {},
        "missing_required": [],
    }
    for asset_name in requested:
        configured_path = Path(str(manifest.get("files", {}).get(asset_name) or "")).resolve()
        direct, direct_meta = _load_asset_with_meta(configured_path)
        direct_non_empty = _asset_non_empty(direct)
        fallback, fallback_meta = ({}, {"loader": "missing", "parse_error": None})
        if pipeline_path.is_file():
            pipeline_asset, pipeline_file_meta = _load_asset_with_meta(pipeline_path)
            fallback = _load_from_pipeline_knowledge_data(pipeline_asset, asset_name)
            fallback_meta = pipeline_file_meta
        fallback_non_empty = _asset_non_empty(fallback)
        loaded_from = "direct" if direct_non_empty else ("pipeline_knowledge" if fallback_non_empty else "empty")
        diagnostics["assets"][asset_name] = {
            "path": str(configured_path),
            "exists": configured_path.is_file(),
            "direct_non_empty": direct_non_empty,
            "fallback_non_empty": fallback_non_empty,
            "loaded_from": loaded_from,
            "direct_loader": direct_meta.get("loader"),
            "direct_parse_error": direct_meta.get("parse_error"),
            "fallback_loader": fallback_meta.get("loader"),
            "fallback_parse_error": fallback_meta.get("parse_error"),
        }
        if loaded_from == "empty":
            diagnostics["missing_required"].append(asset_name)
    diagnostics["all_requested_available"] = not diagnostics["missing_required"]
    return diagnostics
