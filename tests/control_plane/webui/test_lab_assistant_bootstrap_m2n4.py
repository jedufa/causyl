from __future__ import annotations

import json
from pathlib import Path

from infra.webui import server_lab_assistant_bootstrap as bootstrap
from infra.webui import server_lab_assistant_host as host
from infra.webui import server_lab_assistant_state as state
from infra.webui import server_lab_assistant_task_register as register


def _init_repo(root: Path) -> None:
    (root / 'governance' / 'ledger').mkdir(parents=True, exist_ok=True)
    (root / 'governance' / 'agents' / 'runtime').mkdir(parents=True, exist_ok=True)


def _write_json(path: Path, obj: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, sort_keys=True, separators=(',', ':')) + '\n', encoding='utf-8')


def test_bootstrap_reconstructs_register_via_event_log_ready(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    host.bootstrap_host_baseline(tmp_path)

    # Initialize assistant and register, then add one task to produce commit events.
    st = state.load_or_init_assistant_state(tmp_path)
    reg = register.load_or_init_task_register(tmp_path)
    rc = register.upsert_task(
        tmp_path,
        expected_generation=int(reg['write_generation']),
        writer='test.seed',
        task={
            'task_id': 'task_m2n4_event_replay',
            'kind': 'software',
            'title': 'Prove replay from event log',
            'status': 'active',
            'owner': 'assistant',
            'source': 'operator',
            'ledger_uid': 'PIS-20260313-094300Z-M2N4',
            'delegation_refs': [],
            'next_expected_action': 'validate bootstrap reconstructs from BD05 events',
            'blocked_reason': '',
            'created_utc': '2026-03-13T09:43:00Z',
            'updated_utc': '2026-03-13T09:43:00Z',
        },
    )
    assert rc['ok'] and rc['changed']

    # Remove canonical files to force reconstruction from events only.
    register._register_path(tmp_path).unlink(missing_ok=True)
    register._summary_cache_path(tmp_path).unlink(missing_ok=True)

    pkt = bootstrap.build_fresh_turn_bootstrap(tmp_path, recent_chat_rows=[])
    assert pkt['status'] == 'ready'
    assert 'Prove replay from event log' in pkt['text']
    # Event replay should restore assistant linkage and create a fresh summary cache.
    assert pkt['task_register']['assistant_id_ref'] == st['assistant_id']
    assert Path(pkt['task_register']['summary_cache_ref']['path']).exists()


def test_bootstrap_degraded_on_assistant_id_mismatch(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    host.bootstrap_host_baseline(tmp_path)
    st = state.load_or_init_assistant_state(tmp_path)

    # Seed a register with a different assistant_id_ref to simulate conflict.
    bogus_register = {
        'schema_version': register.TASK_REGISTER_SCHEMA_VERSION,
        'register_id': 'task_register_mismatch',
        'assistant_id_ref': 'assistant_deadbeef',
        'write_generation': 1,
        'last_writer': 'test.seed',
        'last_written_utc': '2026-03-13T09:43:00Z',
        'active_tasks': [],
        'open_loops': [],
        'constraints': [],
        'recent_execution_refs': [],
        'summary_cache_ref': {
            'path': str(register._summary_cache_path(tmp_path)),
            'derived_only': True,
            'schema_version': register.TASK_REGISTER_SUMMARY_SCHEMA_VERSION,
        },
        'summary_stale': False,
    }
    _write_json(register._register_path(tmp_path), bogus_register)
    # Ensure summary exists to keep failure specific to mismatch.
    _write_json(register._summary_cache_path(tmp_path), register._build_summary(bogus_register))

    pkt = bootstrap.build_fresh_turn_bootstrap(tmp_path, recent_chat_rows=[])
    assert pkt['status'] == 'degraded'
    assert 'assistant_id_mismatch' in pkt['degraded_reasons']
    assert st['assistant_id'] in pkt['text']


def test_bootstrap_ready_when_summary_cache_missing_is_repaired(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    host.bootstrap_host_baseline(tmp_path)
    st = state.load_or_init_assistant_state(tmp_path)

    # Write a canonical register pointing to a missing summary cache.
    seeded = {
        'schema_version': register.TASK_REGISTER_SCHEMA_VERSION,
        'register_id': 'task_register_missing_summary',
        'assistant_id_ref': st['assistant_id'],
        'write_generation': 2,
        'last_writer': 'test.seed',
        'last_written_utc': '2026-03-13T09:43:00Z',
        'active_tasks': [],
        'open_loops': [],
        'constraints': [],
        'recent_execution_refs': [],
        'summary_cache_ref': {
            'path': str(register._summary_cache_path(tmp_path)),
            'derived_only': True,
            'schema_version': register.TASK_REGISTER_SUMMARY_SCHEMA_VERSION,
        },
        'summary_stale': True,
    }
    _write_json(register._register_path(tmp_path), seeded)
    # Ensure summary cache is absent.
    register._summary_cache_path(tmp_path).unlink(missing_ok=True)

    pkt = bootstrap.build_fresh_turn_bootstrap(tmp_path, recent_chat_rows=[])

    # Bootstrap should rebuild summary and remain ready (no false degrade).
    assert pkt['status'] == 'ready'
    assert 'summary_cache_stale' not in pkt['degraded_reasons']
    assert Path(register._summary_cache_path(tmp_path)).exists()


def test_bootstrap_idempotent_across_repeated_runs(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    host.bootstrap_host_baseline(tmp_path)
    st = state.load_or_init_assistant_state(tmp_path)

    # Seed one stable task via event-producing API.
    reg = register.load_or_init_task_register(tmp_path)
    rc = register.upsert_task(
        tmp_path,
        expected_generation=int(reg['write_generation']),
        writer='test.idempotency',
        task={
            'task_id': 'task_m2n4_idempotent',
            'kind': 'software',
            'title': 'Idempotency check',
            'status': 'active',
            'owner': 'assistant',
            'source': 'operator',
            'ledger_uid': 'PIS-20260313-094300Z-M2N4',
            'delegation_refs': [],
            'next_expected_action': 'call build_fresh_turn_bootstrap twice',
            'blocked_reason': '',
            'created_utc': '2026-03-13T09:43:00Z',
            'updated_utc': '2026-03-13T09:43:00Z',
        },
    )
    assert rc['ok'] and rc['changed']

    first = bootstrap.build_fresh_turn_bootstrap(tmp_path, recent_chat_rows=[])
    second = bootstrap.build_fresh_turn_bootstrap(tmp_path, recent_chat_rows=[])

    assert first['status'] == 'ready' == second['status']
    assert first['degraded_reasons'] == second['degraded_reasons'] == []
    assert first['task_register']['write_generation'] == second['task_register']['write_generation']
    # The rendered bootstrap text should be stable across immediate repeated runs.
    assert first['text'] == second['text']


def test_bootstrap_degraded_when_assistant_state_is_corrupt_but_register_truth_is_still_readable(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    host.bootstrap_host_baseline(tmp_path)
    st = state.load_or_init_assistant_state(tmp_path)

    reg = register.load_or_init_task_register(tmp_path)
    rc = register.upsert_task(
        tmp_path,
        expected_generation=int(reg['write_generation']),
        writer='test.corrupt_assistant_state',
        task={
            'task_id': 'task_m2n4_corrupt_assistant_state',
            'kind': 'software',
            'title': 'Bootstrap should still surface BD05 truth',
            'status': 'interrupted_recovering',
            'owner': 'assistant',
            'source': 'operator',
            'ledger_uid': 'PIS-20260313-094300Z-M2N4',
            'delegation_refs': [],
            'next_expected_action': 'report degraded truth without inventing continuity',
            'blocked_reason': '',
            'created_utc': '2026-03-13T09:43:00Z',
            'updated_utc': '2026-03-13T09:43:00Z',
        },
    )
    assert rc['ok'] and rc['changed']

    assistant_state_path = tmp_path / 'runtime' / 'state' / 'lab_assistant' / 'assistant_state' / 'assistant_state.json'
    assistant_state_path.write_text('{"schema_version":"lab_assistant_state_v1","assistant_id":"broken"', encoding='utf-8')

    pkt = bootstrap.build_fresh_turn_bootstrap(tmp_path, recent_chat_rows=[])

    assert pkt['status'] == 'degraded'
    assert 'assistant_state_unavailable:AssistantStateError' in pkt['degraded_reasons']
    assert 'assistant_state_missing' in pkt['degraded_reasons']
    assert pkt['assistant_state'] is None
    assert pkt['task_register'] is not None
    assert pkt['task_register']['assistant_id_ref'] == st['assistant_id']
    assert pkt['task_register']['residue_tasks'][0]['task_id'] == 'task_m2n4_corrupt_assistant_state'
    assert 'assistant_state: missing' in pkt['text']
    assert 'Bootstrap should still surface BD05 truth' in pkt['text']


def test_bootstrap_degraded_when_task_register_is_corrupt_but_runtime_state_is_still_readable(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    host.bootstrap_host_baseline(tmp_path)
    st = state.load_or_init_assistant_state(tmp_path)
    reg = register.load_or_init_task_register(tmp_path)
    assert reg['assistant_id_ref'] == st['assistant_id']

    register_path = tmp_path / 'runtime' / 'state' / 'lab_assistant' / 'task_register' / 'task_register.json'
    register_path.write_text('{bad\n', encoding='utf-8')

    pkt = bootstrap.build_fresh_turn_bootstrap(tmp_path, recent_chat_rows=[])

    assert pkt['status'] == 'degraded'
    assert 'task_register_unavailable:TaskRegisterError' in pkt['degraded_reasons']
    assert 'task_register_missing' in pkt['degraded_reasons']
    assert pkt['assistant_state'] is not None
    assert pkt['assistant_state']['assistant_id'] == st['assistant_id']
    assert pkt['task_register'] is None
    assert "assistant: Jeff" in pkt["text"]
    assert 'task_register: missing' in pkt['text']
