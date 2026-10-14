"""Offline demonstration using Causyl's actual durable ledger and artifact code."""
from __future__ import annotations

import argparse
import hashlib
import json
import tempfile
from pathlib import Path

from infra.webui.ledger.ledger_manager import ChangeLedger
from experiments.shared.experiment_records import build_artifact_manifest


def run_demo(root: Path) -> dict:
    root.mkdir(parents=True, exist_ok=False)
    ledger = ChangeLedger(repo_root=root)
    item = ledger.create_item(title='Inspect a fictional sample', slug='sample-review',
                              pis_markdown='# Sample review\nMeasure and record a fictional sample.\n')
    uid = item['uid']
    recovered = ChangeLedger(repo_root=root).list_items()
    if len(recovered) != 1 or recovered[0]['uid'] != uid:
        raise RuntimeError('Ledger did not persist across a fresh reader')
    payload = root / 'sample-output'
    payload.mkdir()
    result = payload / 'measurement.json'
    result.write_text(json.dumps({'sample': 'fictional-A', 'measurement': 42}) + '\n')
    manifest = build_artifact_manifest(str(payload), 'demo-job', 'demo-spec')
    expected = hashlib.sha256(result.read_bytes()).hexdigest()
    if manifest['files'][0]['sha256'] != expected:
        raise RuntimeError('Artifact digest does not match independent calculation')
    result.write_text('changed after measurement\n')
    tampered = hashlib.sha256(result.read_bytes()).hexdigest()
    if tampered == manifest['files'][0]['sha256']:
        raise RuntimeError('Negative control failed: changed bytes went undetected')
    return {'ok': True, 'ledger_persisted': True, 'artifact_digest_verified': True,
            'tamper_negative_control_detected': True, 'network_or_agent_calls': 0}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, help='New directory for fictional demo data; must not already exist')
    args = parser.parse_args()
    if args.output:
        result = run_demo(args.output.resolve())
    else:
        with tempfile.TemporaryDirectory(prefix='causyl-demo-') as temp:
            result = run_demo(Path(temp) / 'demo')
    print(json.dumps(result, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
