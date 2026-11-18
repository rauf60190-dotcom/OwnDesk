import base64
import importlib.util
import json
from pathlib import Path
import sys
from unittest.mock import patch

import pytest

INFRA = Path(__file__).resolve().parents[1] / 'infra'
sys.path.insert(0, str(INFRA))
spec = importlib.util.spec_from_file_location('windows_recovery', INFRA / 'windows_recovery.py')
recovery = importlib.util.module_from_spec(spec)
spec.loader.exec_module(recovery)
import windows_recovery_ui as client


def backup(directory):
    key = base64.urlsafe_b64encode(b'k' * 32).decode()
    values = {'database.dump': b'test-only-dump',
        'config/secrets.json': json.dumps({'SECRET_KEY': 'test-only-session', 'ACCOUNT_ENCRYPTION_KEY': key,
            'TOKEN_ENCRYPTION_KEY': key, 'POSTGRES_ADMIN_PASSWORD': 'original-admin', 'POSTGRES_APP_PASSWORD': 'original-app'}).encode(),
        'config/runtime-profile.json': b'{"public_hostname":"not-used-in-quarantine"}',
        'config/portal-connection.json': b'{"connector_key":"must-stay-admin-only"}',
        'config/root-tunnel.token': b'must-not-start', 'config/cloudflare.json': b'{"token":"must-stay-admin-only"}',
        'private-attachments/sample.jpg': b'test-image'}
    files = {}
    for name, content in values.items():
        path = directory / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        files[name] = recovery.fingerprint(path)
    manifest = {'format': 1, 'kind': 'organization', 'version': '0.4.26', 'ready_attachments': 1,
        'counts': {'attachments_attachment': 1}, 'files': files}
    (directory / 'manifest.json').write_text(json.dumps(manifest))
    return manifest


def test_recovery_preserves_keys_and_files_but_not_live_connections(tmp_path):
    source, target = tmp_path / 'backup', tmp_path / 'recovered'
    source.mkdir(); target.mkdir()
    original = backup(source)
    recovery.stage(source, target, tmp_path / 'postgres/bin', 15590)
    probe = target / 'probe'
    assert (probe / 'private-attachments/sample.jpg').read_bytes() == b'test-image'
    original_keys = json.loads((source / 'config/secrets.json').read_text())
    restored = json.loads((probe / 'secrets.json').read_text())
    assert restored['ACCOUNT_ENCRYPTION_KEY'] == original_keys['ACCOUNT_ENCRYPTION_KEY']
    assert restored['POSTGRES_ADMIN_PASSWORD'] != original_keys['POSTGRES_ADMIN_PASSWORD']
    assert restored['POSTGRES_APP_PASSWORD'] != original_keys['POSTGRES_APP_PASSWORD']
    for name in ('runtime-profile.json', 'portal-connection.json', 'integrations.json', 'cloudflare.json', 'root-tunnel.token'):
        assert not (probe / name).exists()
    assert (target / 'original-config/root-tunnel.token').read_bytes() == b'must-not-start'
    profile = json.loads((probe / 'probe-profile.json').read_text())
    assert profile['quarantine'] and profile['postgres']['database'] == 'owndesk_recovery'
    assert profile['initial_restore']['sha256'] == original['files']['database.dump']['sha256']


@pytest.mark.parametrize('port', [55485, 55486, 8000, 70000, True])
def test_live_or_invalid_ports_are_never_used(tmp_path, port):
    source, target = tmp_path / 'backup', tmp_path / 'output'
    source.mkdir(); target.mkdir(); backup(source)
    with pytest.raises(ValueError, match='port'):
        recovery.stage(source, target, tmp_path / 'bin', port)
    assert list(target.iterdir()) == []


def test_changed_backup_or_existing_destination_is_not_overwritten(tmp_path):
    source, target = tmp_path / 'backup', tmp_path / 'output'
    source.mkdir(); target.mkdir(); backup(source)
    with pytest.raises(ValueError, match='selection changed'):
        recovery.stage(source, target, tmp_path / 'bin', 15590, '0' * 64)
    (target / 'keep').write_text('original')
    with pytest.raises(ValueError, match='empty'):
        recovery.stage(source, target, tmp_path / 'bin', 15590)
    assert (target / 'keep').read_text() == 'original'


def test_invalid_table_names_are_rejected_before_sql_restore(tmp_path):
    backup(tmp_path)
    manifest = json.loads((tmp_path / 'manifest.json').read_text())
    manifest['counts'] = {'unsafe;drop table': 1}
    (tmp_path / 'manifest.json').write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match='table counts'):
        recovery.inspect_backup(tmp_path)


def test_client_requires_a_fresh_verified_backup():
    state = client.CheckedBackup()
    assert not state.matches('somewhere')
    state.accept('somewhere', {'status': 'backup-verified', 'kind': 'organization', 'manifest_sha256': 'a' * 64})
    assert state.matches('somewhere') and not state.matches('elsewhere')
    state.clear()
    assert not state.matches('somewhere')


def test_client_rejects_unconfirmed_quarantine_result(tmp_path):
    backup(tmp_path)
    with patch.object(client, 'run_process', return_value={'status': 'verified'}), pytest.raises(client.UpdateError):
        client.run(str(tmp_path), 'recover', lambda _: None, 'a' * 64)
