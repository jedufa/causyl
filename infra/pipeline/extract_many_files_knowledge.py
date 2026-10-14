from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, Mapping

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from infra.pipeline.knowledge_assets import _load_minimal_yaml

BUNDLE_NAME = 'many_files_to_split.yaml'
PROTECTED_CANONICAL_FILES = {
    'bundle_coverage_rules.yaml',
    'conceptual_query_templates.yaml',
    'confidence_rules.yaml',
    'cross_cutting_flags.yaml',
    'derived_requirements.yaml',
    'failure_taxonomy.yaml',
    'invariant_templates.yaml',
    'negative_targets.yaml',
    'operator_action_taxonomy.yaml',
    'passive_relevance_patterns.yaml',
    'patch_contract_templates.yaml',
    'pipeline_knowledge.yaml',
    'retrieval_bias_rules.yaml',
    'role_assignment_rules.yaml',
    'scoring_weights.yaml',
    'state_transition_patterns.yaml',
    'subsystem_map.yaml',
    'task_families.yaml',
    'task_profile_rules.yaml',
}
DIRECT_BUNDLE_FILES = {
    'concurrency_signal_terms.yaml': (0, 'Deterministic concurrency signal catalog extracted from many_files_to_split.'),
    'lexical_clue_terms.yaml': (0, 'Deterministic lexical clue catalog extracted from many_files_to_split.'),
    'mutation_signal_terms.yaml': (0, 'Deterministic mutation signal catalog extracted from many_files_to_split.'),
    'observability_signal_terms.yaml': (0, 'Deterministic observability signal catalog extracted from many_files_to_split.'),
    'recovery_signal_terms.yaml': (0, 'Deterministic recovery signal catalog extracted from many_files_to_split.'),
    'responsibility_terms.yaml': (0, 'Deterministic responsibility-term catalog extracted from many_files_to_split.'),
    'scheduler_safety.yaml': (1, 'Deterministic scheduler safety rule catalog extracted from many_files_to_split.'),
    'subsystem_aliases.yaml': (0, 'Deterministic subsystem alias catalog extracted from many_files_to_split.'),
    'validation_signal_terms.yaml': (0, 'Deterministic validation signal catalog extracted from many_files_to_split.'),
}


def _parse_bundle(path: Path) -> Dict[str, Mapping[str, Any]]:
    text = path.read_text(encoding='utf-8')
    pattern = re.compile(r'^FILE: (?P<name>.+?)\r?\nYAML\r?\n\r?\n', re.M)
    matches = list(pattern.finditer(text))
    out: Dict[str, Mapping[str, Any]] = {}
    for index, match in enumerate(matches):
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        body = text[start:end].rstrip()
        if not body:
            continue
        parsed = _load_payload(body)
        if isinstance(parsed, Mapping):
            out[match.group('name').strip()] = dict(parsed)
    return out


def _load_payload(body: str) -> Mapping[str, Any]:
    lines = body.splitlines()
    while lines:
        candidate = "\n".join(lines).strip()
        if not candidate:
            break
        try:
            parsed = _load_minimal_yaml(candidate)
        except ValueError:
            lines.pop()
            continue
        if isinstance(parsed, Mapping):
            return dict(parsed)
        break
    return {}


def _quote(text: str) -> str:
    if text == '':
        return '""'
    safe = re.fullmatch(r'[A-Za-z0-9_./-]+', text) is not None
    reserved = text.lower() in {'true', 'false', 'null', 'none', '~'}
    if safe and not reserved and not text.lstrip('-').isdigit():
        return text
    escaped = text.replace('\\', '\\\\').replace('"', '\\"')
    return f'"{escaped}"'


def _dump_yaml(value: Any, indent: int = 0) -> str:
    pad = ' ' * indent
    if isinstance(value, Mapping):
        if not value:
            return '{}'
        lines = []
        for key, item in value.items():
            key_text = _quote(str(key)) if not re.fullmatch(r'[A-Za-z0-9_./-]+', str(key)) else str(key)
            if isinstance(item, Mapping):
                if item:
                    lines.append(f'{pad}{key_text}:')
                    lines.append(_dump_yaml(item, indent + 2))
                else:
                    lines.append(f'{pad}{key_text}: {{}}')
            elif isinstance(item, list):
                if item:
                    lines.append(f'{pad}{key_text}:')
                    lines.append(_dump_yaml(item, indent + 2))
                else:
                    lines.append(f'{pad}{key_text}: []')
            else:
                lines.append(f'{pad}{key_text}: {_dump_scalar(item)}')
        return '\n'.join(lines)
    if isinstance(value, list):
        if not value:
            return f'{pad}[]'
        lines = []
        for item in value:
            if isinstance(item, Mapping):
                if not item:
                    lines.append(f'{pad}- {{}}')
                    continue
                first = True
                for key, subvalue in item.items():
                    key_text = _quote(str(key)) if not re.fullmatch(r'[A-Za-z0-9_./-]+', str(key)) else str(key)
                    prefix = f'{pad}- ' if first else f'{pad}  '
                    if isinstance(subvalue, Mapping):
                        if subvalue:
                            lines.append(f'{prefix}{key_text}:')
                            lines.append(_dump_yaml(subvalue, indent + 4))
                        else:
                            lines.append(f'{prefix}{key_text}: {{}}')
                    elif isinstance(subvalue, list):
                        if subvalue:
                            lines.append(f'{prefix}{key_text}:')
                            lines.append(_dump_yaml(subvalue, indent + 4))
                        else:
                            lines.append(f'{prefix}{key_text}: []')
                    else:
                        lines.append(f'{prefix}{key_text}: {_dump_scalar(subvalue)}')
                    first = False
            elif isinstance(item, list):
                if item:
                    lines.append(f'{pad}-')
                    lines.append(_dump_yaml(item, indent + 2))
                else:
                    lines.append(f'{pad}- []')
            else:
                lines.append(f'{pad}- {_dump_scalar(item)}')
        return '\n'.join(lines)
    return f'{pad}{_dump_scalar(value)}'


def _dump_scalar(value: Any) -> str:
    if value is None:
        return 'null'
    if isinstance(value, bool):
        return 'true' if value else 'false'
    if isinstance(value, (int, float)):
        return str(value)
    return _quote(str(value))


def _load_current(path: Path) -> Dict[str, Any]:
    parsed = _load_minimal_yaml(path.read_text(encoding='utf-8'))
    return dict(parsed) if isinstance(parsed, Mapping) else {}


def _build_protected(current: Dict[str, Any], payload: Mapping[str, Any], asset_name: str) -> Dict[str, Any]:
    out = dict(current)
    out.pop('supplemental', None)
    out['supplemental'] = {
        'extracted_from_bundle': {
            'source_file': BUNDLE_NAME,
            'asset_name': asset_name,
            'payload': dict(payload),
        }
    }
    return out


def _build_direct(current: Dict[str, Any], payload: Mapping[str, Any], asset_name: str) -> Dict[str, Any]:
    phase, notes = DIRECT_BUNDLE_FILES[asset_name]
    out: Dict[str, Any] = {
        'version': 1,
        'phase': phase,
        'notes': notes,
    }
    out.update(dict(payload))
    return out


def _rewrite_file(path: Path, data: Mapping[str, Any]) -> None:
    text = _dump_yaml(data).rstrip() + '\n'
    path.write_text(text, encoding='utf-8')


def _iter_targets(knowledge_dir: Path) -> Iterable[Path]:
    for path in sorted(knowledge_dir.glob('*.yaml')):
        if path.name == BUNDLE_NAME:
            continue
        yield path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--knowledge-dir', default='infra/pipeline/knowledge')
    args = parser.parse_args()

    knowledge_dir = Path(args.knowledge_dir).resolve()
    bundle_path = knowledge_dir / BUNDLE_NAME
    bundle = _parse_bundle(bundle_path)
    for path in _iter_targets(knowledge_dir):
        current = _load_current(path)
        payload = bundle.get(path.name)
        if path.name in DIRECT_BUNDLE_FILES and payload is not None:
            final = _build_direct(current, payload, path.name)
        elif path.name in PROTECTED_CANONICAL_FILES and payload is not None:
            final = _build_protected(current, payload, path.name)
        else:
            final = current
        _rewrite_file(path, final)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
