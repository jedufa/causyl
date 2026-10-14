"""Export an explicit, reviewable source snapshot without operational Git history."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import stat
from pathlib import Path
from tools.release.screenshot_policy import SCREENSHOTS, validate_png, validate_screenshot_set

ROOT = Path(__file__).resolve().parents[2]
OVERRIDES = {'.gitignore': 'tools/release/templates/gitignore', 'README.md': 'tools/release/templates/README.md', 'AGENTS.md': 'tools/release/templates/AGENTS.md'}
BANNED_PARTS = {'.git', '.env', '.tooling', '__pycache__', 'node_modules', 'knowledge_drafts', 'sessions', 'transcripts'}
BANNED_PREFIXES = ('runtime/', 'governance/ledger/', 'governance/evidence/', 'experiments/life_os/', 'experiments/projects/emergence/', 'docs/90_archive/', 'tools/env_clone/manifests/')
BANNED_SUFFIXES = {'.pdf', '.epub', '.docx', '.xlsx', '.zip', '.jar', '.apk', '.db', '.sqlite', '.png', '.jpg', '.jpeg', '.webp', '.pyc', '.pem', '.key', '.jks', '.jsonl'}


def allowed_path(name: str) -> bool:
    p = Path(name)
    return bool(name) and p.as_posix() == name and not p.is_absolute() and '..' not in p.parts and not any(x in BANNED_PARTS for x in p.parts) and not name.startswith(BANNED_PREFIXES) and (p.suffix.lower() not in BANNED_SUFFIXES or name in SCREENSHOTS)


def normalize_examples(text: str) -> str:
    text = re.sub(r'(?:/home|/Users)/[A-Za-z0-9._-]+', '/home/user', text)
    text = re.sub(r'\b192\.168\.\d{1,3}\.\d{1,3}\b', '192.0.2.1', text)
    text = re.sub(r'\b10\.\d{1,3}\.\d{1,3}\.\d{1,3}\b', '192.0.2.1', text)
    text = re.sub(r'\b172\.(?:1[6-9]|2\d|3[01])\.\d{1,3}\.\d{1,3}\b', '192.0.2.1', text)
    text = re.sub(r'\b[A-Za-z0-9._-]+\.(?:local|internal|lan)\b(?![.A-Za-z0-9_-])', 'host.example.invalid', text)
    text = re.sub(r'(?<![\w/])\b[A-Za-z0-9][A-Za-z0-9._%+-]*@(?!example\.(?:com|org|net|invalid)\b)[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b', 'operator@example.invalid', text)
    return text


def export(destination: Path, *, source: Path = ROOT) -> dict:
    names = sorted(set((source / 'tools/release/source_allowlist.txt').read_text().splitlines()))
    if any(not allowed_path(name) for name in names):
        raise ValueError('Export allowlist contains a forbidden path')
    if destination.exists():
        raise FileExistsError('Export destination must be new')
    validate_screenshot_set(source, names)
    records = []
    prepared = []
    for name in names:
        path = source / OVERRIDES.get(name, name)
        resolved = path.resolve(strict=True)
        if not resolved.is_relative_to(source.resolve()):
            raise ValueError('Export input escapes source root: ' + name)
        if path.is_symlink():
            target_name = resolved.relative_to(source.resolve()).as_posix()
            if target_name not in names:
                raise ValueError('Symlink points outside allowlist: ' + name)
        original = path.read_bytes()
        if name in SCREENSHOTS:
            validate_png(original)
            data = original
        else:
            text = original.decode('utf-8')
            if '\0' in text:
                raise ValueError('Binary input rejected: ' + name)
            # Preserve third-party launchers exactly, including all attribution.
            data = original if name.endswith(('gradlew', 'gradlew.bat')) else normalize_examples(text).encode()
        mode = 0o755 if path.stat().st_mode & stat.S_IXUSR else 0o644
        records.append({'path': name, 'sha256': hashlib.sha256(data).hexdigest(), 'bytes': len(data),
                        'mode': mode, 'normalized': data != original})
        prepared.append((name, data, mode))
    destination.mkdir(parents=True)
    for name, data, mode in prepared:
        path = destination / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        path.chmod(mode)
    manifest = {'format': 'causyl-source-preview-v1', 'files': records,
                'normalization': 'Machine home paths, private network addresses, private hostnames and non-example email literals normalized in export copies. Symlinks materialized only when their target is also allowlisted.',
                'original_history_included': False}
    (destination / 'SOURCE_MANIFEST.json').write_text(json.dumps(manifest, indent=2) + '\n')
    return {'files': len(records), 'bytes': sum(r['bytes'] for r in records), 'normalized_files': sum(r['normalized'] for r in records)}


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('destination', type=Path)
    args = p.parse_args()
    print(json.dumps(export(args.destination.resolve()), indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
