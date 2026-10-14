"""Check the exact export's membership, digests and bounded privacy signatures."""
from __future__ import annotations
import argparse
import hashlib
import json
import re
import zlib
from pathlib import Path
from tools.release.export_source import allowed_path
from tools.release.screenshot_policy import SCREENSHOTS, validate_png, validate_screenshot_set

PATTERNS = {
    'private_key': re.compile(r'-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----'),
    'provider_token': re.compile(r'\b(?:sk-(?:proj-|ant-)?[A-Za-z0-9_-]{20,}|gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|AKIA[A-Z0-9]{16})\b'),
    'private_home': re.compile(r'(?:/home|/Users)/(?!user\b)[A-Za-z0-9._-]+'),
    'private_ip': re.compile(r'\b(?:192\.168\.\d+\.\d+|10\.\d+\.\d+\.\d+|172\.(?:1[6-9]|2\d|3[01])\.\d+\.\d+)\b'),
    'conversation_link': re.compile(r'https://chatgpt\.com/(?:c/|g/)[^\s]+'),
    'credential_url': re.compile(r'(?:https?|ssh|ftp)://[^\s/"\x27]+:[^\s/"\x27]+@'),
    'machine_identity': re.compile(r'(?im)^\s*(?:HOSTNAME|MACHINE_ID|BOOT_ID|DMI_PRODUCT_UUID|DMI_PRODUCT_SERIAL)\s*[:=]\s*["\x27]?[A-Za-z0-9][A-Za-z0-9_.:-]*["\x27]?\s*$'),
}


def scan_text(text: str) -> list[str]:
    return [name for name, pattern in PATTERNS.items() if pattern.search(text)]


def audit(root: Path) -> dict:
    manifest = json.loads((root / 'SOURCE_MANIFEST.json').read_text())
    failures = []
    names = set()
    for row in manifest['files']:
        name = row['path']
        path = root / name
        names.add(name)
        if not allowed_path(name) or path.is_symlink() or not path.resolve().is_relative_to(root.resolve()):
            failures.append({'path': name, 'reason': 'unsafe path'})
            continue
        if not path.is_file():
            failures.append({'path': name, 'reason': 'missing'})
            continue
        data = path.read_bytes()
        if hashlib.sha256(data).hexdigest() != row['sha256'] or len(data) != row['bytes']:
            failures.append({'path': name, 'reason': 'hash or size mismatch'})
        try:
            if name in SCREENSHOTS:
                validate_png(data)
            else:
                for category in scan_text(data.decode('utf-8')):
                    failures.append({'path': name, 'reason': category})
        except (ValueError, zlib.error) as exc:
            failures.append({'path': name, 'reason': str(exc)})
    extra = [p.relative_to(root).as_posix() for p in root.rglob('*') if p.is_file()
             and '.git' not in p.relative_to(root).parts and '__pycache__' not in p.parts
             and '.pytest_cache' not in p.parts and p.relative_to(root).as_posix() not in names | {'SOURCE_MANIFEST.json'}]
    failures.extend({'path': n, 'reason': 'unlisted file'} for n in extra)
    try:
        validate_screenshot_set(root, names)
    except (ValueError, OSError, KeyError, zlib.error) as exc:
        failures.append({'path': 'docs/screenshots', 'reason': str(exc)})
    return {'ok': not failures, 'files': len(names), 'failures': failures,
            'scope': 'Manifest integrity and bounded text signatures; not exhaustive secret, rights or privacy proof.'}


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('root', type=Path)
    args = p.parse_args()
    result = audit(args.root)
    print(json.dumps(result, indent=2))
    return 0 if result['ok'] else 1

if __name__ == '__main__':
    raise SystemExit(main())
