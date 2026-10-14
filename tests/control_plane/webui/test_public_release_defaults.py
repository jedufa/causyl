from __future__ import annotations

import io
from types import SimpleNamespace

import pytest

from infra.webui import server_routes_and_main as routes
from infra.webui.server_http_core import _admin_guard
from tools.release.demo import run_demo
from tools.release.export_source import allowed_path, export


@pytest.mark.parametrize('argv,message', [([], 'disabled by default'),
    (['--enable-operator-runtime','--host','0.0.0.0'], 'loopback'),
    (['--enable-operator-runtime'], 'EIEL_ADMIN_TOKEN')])
def test_runtime_rejected_before_bootstrap(argv, message, monkeypatch):
    monkeypatch.delenv('EIEL_ADMIN_TOKEN', raising=False)
    monkeypatch.setattr(routes, '_bootstrap_lab_assistant_host', lambda **_: pytest.fail('unexpected bootstrap'))
    with pytest.raises(SystemExit, match=message):
        routes.main(argv)


def test_admin_without_token_fails_closed(tmp_path):
    statuses = []
    request = SimpleNamespace(headers={}, wfile=io.BytesIO(),
        send_response=lambda status: statuses.append(int(status)),
        send_header=lambda *args: None, end_headers=lambda: None)
    cfg = routes.WebUIConfig(runs_root=tmp_path, admin_enabled=True, admin_token='')
    assert not _admin_guard(request, cfg)
    assert statuses == [403]


def test_admin_accepts_explicit_matching_token(tmp_path):
    request = SimpleNamespace(headers={'X-Admin-Token':'fictional-test-token'})
    cfg = routes.WebUIConfig(runs_root=tmp_path, admin_enabled=True, admin_token='fictional-test-token')
    assert _admin_guard(request, cfg)


def test_demo_persists_and_detects_tampering(tmp_path):
    assert run_demo(tmp_path / 'demo')['tamper_negative_control_detected']
    with pytest.raises(FileExistsError):
        run_demo(tmp_path / 'demo')


@pytest.mark.parametrize('path', ['../private', '/tmp/private', 'runtime/key.json',
    'governance/ledger/private.txt', 'infra/.env', 'reference/book.pdf'])
def test_export_denies_private_paths(path):
    assert not allowed_path(path)


def test_export_rejects_escape_symlink_before_writing(tmp_path):
    src = tmp_path / 'source'
    (src / 'tools/release').mkdir(parents=True)
    (src / 'tools/release/source_allowlist.txt').write_text('safe.py\n')
    (tmp_path / 'outside.py').write_text('private = True\n')
    (src / 'safe.py').symlink_to(tmp_path / 'outside.py')
    dest = tmp_path / 'destination'
    with pytest.raises(ValueError, match='escapes source root'):
        export(dest, source=src)
    assert not dest.exists()


def test_privacy_scanner_negative_controls():
    from tools.release.audit import scan_text
    assert 'provider_token' in scan_text('gh' + 'p_' + 'A' * 30)
    assert 'private_key' in scan_text('-----BEGIN ' + 'PRIVATE KEY-----')
    assert 'private_home' in scan_text('/home/' + 'fictional_private_owner/file')
    assert 'private_ip' in scan_text('192.' + '168.55.9')
    assert scan_text('Use /home/user and operator@example.invalid.') == []


def test_captured_environment_inventory_and_path_aliases_are_excluded():
    for path in ('tools/env_clone/manifests/system_identity.txt',
                 'tools/env_clone/manifests/apt_sources/sources/sources.list',
                 './tools/env_clone/manifests/system_identity.txt',
                 'tools//env_clone/manifests/system_identity.txt'):
        assert not allowed_path(path)
    assert allowed_path('tools/env_clone/scripts/snapshot.sh')
    assert allowed_path('tools/env_clone/profiles/server_minimal.txt')


def test_reallowlisted_environment_inventory_is_rejected_before_export(tmp_path):
    source = tmp_path / 'source'
    (source / 'tools/release').mkdir(parents=True)
    name = 'tools/env_clone/manifests/system_identity.txt'
    (source / name).parent.mkdir(parents=True)
    (source / name).write_text('HOST' + 'NAME=fictional-test-machine\n')
    (source / 'tools/release/source_allowlist.txt').write_text(name + '\n')
    destination = tmp_path / 'candidate'
    with pytest.raises(ValueError, match='forbidden path'):
        export(destination, source=source)
    assert not destination.exists()


def test_privacy_scanner_rejects_literal_machine_identity():
    from tools.release.audit import scan_text
    for key, value in [('HOSTNAME', 'fictional-test-machine'),
                       ('MACHINE_ID', '0' * 32), ('BOOT_ID', '1' * 32),
                       ('DMI_PRODUCT_UUID', '00000000-0000-0000-0000-000000000000'),
                       ('DMI_PRODUCT_SERIAL', 'fictional-serial')]:
        assert 'machine_identity' in scan_text(key + '=' + value + '\n')
    assert 'machine_identity' not in scan_text('HOSTNAME=$(hostname)\n')
    assert 'machine_identity' not in scan_text('echo "HOSTNAME=$(hostname)"\n')
