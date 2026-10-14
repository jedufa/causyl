from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import yaml

from .models import KnowledgeModel
from .utils import (
    GENERIC_META_KEYS,
    is_noise_term,
    iter_term_forms,
    list_yaml_files,
    looks_like_path_pattern,
    normalize_compound,
    read_text_safe,
)

ALIAS_KEYS = {"alias", "aliases", "synonym", "synonyms", "variant", "variants", "terms", "names"}
CANONICAL_KEYS = {"canonical", "key", "name", "subsystem", "family", "bucket", "term"}


class KnowledgeLoader:
    def __init__(self, knowledge_root: Path, *, logger: logging.Logger) -> None:
        self.knowledge_root = knowledge_root
        self.logger = logger

    def load(self) -> KnowledgeModel:
        model = KnowledgeModel(knowledge_root=self.knowledge_root)
        if not self.knowledge_root.exists():
            self.logger.warning("Knowledge root does not exist: %s. Continuing with an empty knowledge model.", self.knowledge_root)
            return model

        yaml_files = list_yaml_files(self.knowledge_root)
        model.yaml_files.extend(yaml_files)
        self.logger.info("Loading knowledge database from %s (%s YAML files)", self.knowledge_root, len(yaml_files))

        for path in yaml_files:
            try:
                data = yaml.safe_load(read_text_safe(path))
            except Exception as exc:  # noqa: BLE001
                self.logger.warning("Skipping unreadable YAML %s: %s", path, exc)
                continue
            if data is None:
                continue
            self._extract_generic_terms(model, data, current_path=[])
            self._extract_synonym_relations(model, data, parent_key=None)

        self.logger.info(
            "Knowledge model loaded: %s known terms, %s canonical terms, %s explicit synonym groups",
            len(model.known_terms),
            len(model.canonical_terms),
            len(model.synonym_map),
        )
        return model

    def _extract_generic_terms(self, model: KnowledgeModel, node: Any, current_path: list[str]) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                if isinstance(key, str):
                    normalized_key = normalize_compound(key)
                    if normalized_key and normalized_key not in GENERIC_META_KEYS:
                        self._register_string(model, key, canonical=len(current_path) <= 1 and normalized_key not in GENERIC_META_KEYS)
                    if normalized_key in {"path", "paths", "pattern", "patterns", "glob", "globs", "directory", "directories"}:
                        self._extract_path_patterns(model, value)
                    if normalized_key in CANONICAL_KEYS and isinstance(value, str):
                        self._register_string(model, value, canonical=True)
                self._extract_generic_terms(model, value, current_path + [str(key)])
            return

        if isinstance(node, list):
            for item in node:
                self._extract_generic_terms(model, item, current_path)
            return

        if isinstance(node, str):
            self._register_string(model, node, canonical=False)
            if looks_like_path_pattern(node):
                model.known_path_patterns.add(node)
                for form in iter_term_forms(node):
                    if not is_noise_term(form):
                        model.known_path_tokens.add(form)

    def _extract_path_patterns(self, model: KnowledgeModel, value: Any) -> None:
        if isinstance(value, str):
            if looks_like_path_pattern(value):
                model.known_path_patterns.add(value)
                for form in iter_term_forms(value):
                    if not is_noise_term(form):
                        model.known_path_tokens.add(form)
            return
        if isinstance(value, list):
            for item in value:
                self._extract_path_patterns(model, item)
            return
        if isinstance(value, dict):
            for nested in value.values():
                self._extract_path_patterns(model, nested)

    def _register_string(self, model: KnowledgeModel, raw_value: str, *, canonical: bool) -> None:
        raw_value = raw_value.strip()
        if not raw_value:
            return
        forms = list(iter_term_forms(raw_value))
        if not forms:
            return
        for form in forms:
            if is_noise_term(form):
                continue
            model.register_known_term(form, raw_value, canonical=canonical)

    def _extract_synonym_relations(self, model: KnowledgeModel, node: Any, parent_key: str | None) -> None:
        if isinstance(node, dict):
            lowered_keys = {str(key).lower(): key for key in node.keys()}

            canonical_value: str | None = None
            for candidate_key in CANONICAL_KEYS:
                original_key = lowered_keys.get(candidate_key)
                if original_key is not None and isinstance(node[original_key], str):
                    canonical_value = node[original_key]
                    self._register_string(model, canonical_value, canonical=True)
                    break

            for raw_key, value in node.items():
                key = str(raw_key)
                lowered = key.lower()
                if lowered in ALIAS_KEYS:
                    canonical_source = canonical_value or parent_key
                    if canonical_source and isinstance(value, list):
                        for item in value:
                            if isinstance(item, str):
                                self._register_string(model, item, canonical=False)
                                canonical_norm = normalize_compound(canonical_source)
                                item_norm = normalize_compound(item)
                                if canonical_norm and item_norm:
                                    model.register_synonym(canonical_norm, item_norm)
                    elif canonical_source and isinstance(value, str):
                        self._register_string(model, value, canonical=False)
                        canonical_norm = normalize_compound(canonical_source)
                        value_norm = normalize_compound(value)
                        if canonical_norm and value_norm:
                            model.register_synonym(canonical_norm, value_norm)
                next_parent = canonical_value or (key if lowered not in ALIAS_KEYS else parent_key)
                self._extract_synonym_relations(model, value, parent_key=next_parent)
            return

        if isinstance(node, list):
            for item in node:
                self._extract_synonym_relations(model, item, parent_key=parent_key)
