import importlib.util
import json
import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

INFRA = Path(__file__).resolve().parents[1] / 'infra'


def load(name):
    spec = importlib.util.spec_from_file_location(name, INFRA / (name + '.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


backup = load('linux_backup')
schedule = load('linux_backup_schedule')
setup = load('linux_setup')


@pytest.fixture
def copy(tmp_path):
    root = tmp_path / 'copy'
    root.mkdir(mode=0o700)
    settings = {'project': 'owndesk-' + 'a' * 12, 'db_admin_password': 'b' * 43, 'db_app_password': 'c' * 43}
    files = {}
    for name in backup.REQUIRED:
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        path.write_text(json.dumps(settings) if name == 'installer/installation.json' else 'fixture')
        path.chmod(0o600)
        files[name] = backup.fingerprint(path)
    manifest = {'format': 1, 'kind': 'linux-compose', 'version': '0.4.33', 'project': settings['project'],
                'app_image': 'sha256:' + 'a' * 64, 'pg_image': 'sha256:' + 'b' * 64,
                'files': files, 'counts': {'tickets_ticket': 1}}
    backup.atomic(root / 'manifest.json', manifest)
    return root, manifest


def test_verify_private_backup(copy):
    root, manifest = copy
    assert backup.verify(root) == manifest


def test_corruption_rejected_before_docker(copy):
    root, _ = copy
    (root / 'database.dump').write_bytes(b'changed')
    with patch.object(backup, 'Docker') as docker, pytest.raises(ValueError, match='checksum'):
        backup.restore_check(root)
    docker.assert_not_called()


@pytest.mark.parametrize('name', ['../escape', '/absolute', 'data/../escape', 'data//empty', 'data\\file', 'C:/file'])
def test_manifest_path_escape_rejected(copy, name):
    root, manifest = copy
    manifest['files'][name] = {'size': 0, 'sha256': 'a' * 64}
    backup.atomic(root / 'manifest.json', manifest)
    with pytest.raises((ValueError, OSError)):
        backup.verify(root)


def test_hardlinked_secret_rejected(copy, tmp_path):
    root, _ = copy
    os.link(root / 'data/secrets.json', tmp_path / 'alias')
    with pytest.raises(ValueError, match='regular file'):
        backup.verify(root)


def test_unlisted_file_rejected(copy):
    root, _ = copy
    (root / 'unexpected').write_text('not in manifest')
    with pytest.raises(ValueError, match='Unlisted'):
        backup.verify(root)


def test_symlink_rejected(copy, tmp_path):
    root, _ = copy
    path = root / 'database.dump'
    path.unlink()
    target = tmp_path / 'elsewhere'
    target.write_text('fixture')
    try:
        path.symlink_to(target)
    except OSError:
        pytest.skip('Host does not allow unprivileged symbolic links')
    with pytest.raises(ValueError, match='links'):
        backup.verify(root)


@pytest.mark.parametrize('at', ['24:00', '2:30', '12:60', '02:30\nExecStart=/bad'])
def test_schedule_rejects_invalid_calendar(at, tmp_path):
    installation = SimpleNamespace(project='owndesk-' + 'a' * 12, root=tmp_path)
    with pytest.raises(ValueError, match='HH:MM'):
        schedule.unit_contents(installation, tmp_path / 'copies', at)


def test_schedule_has_recovery_and_no_shell_or_secret(tmp_path):
    installation = SimpleNamespace(project='owndesk-' + 'a' * 12, root=tmp_path / 'path with % $ spaces')
    units = schedule.unit_contents(installation, tmp_path / 'backups', '02:30')
    service = units['owndesk-backup-' + 'a' * 12 + '.service']
    recover = units['owndesk-backup-' + 'a' * 12 + '-resume.service']
    assert 'ExecStopPost=:/usr/bin/python3 ' in service
    assert ' resume --root ' in service and ' backup --root ' in service
    assert 'UMask=0077' in service and 'Persistent=true' in units['owndesk-backup-' + 'a' * 12 + '.timer']
    assert 'WantedBy=multi-user.target' in recover
    assert '%%' in service and '/bin/sh' not in service and 'PASSWORD' not in service


def test_foreign_systemd_file_is_never_overwritten(tmp_path):
    installation = SimpleNamespace(project='owndesk-' + 'a' * 12)
    path = tmp_path / ('owndesk-backup-' + 'a' * 12 + '.service')
    path.write_text('unrelated service')
    with patch.object(schedule, 'SYSTEM', tmp_path), pytest.raises(ValueError, match='unrelated'):
        schedule.owned_files(installation)
    assert path.read_text() == 'unrelated service'


def test_unknown_recovery_container_rejected_before_start(tmp_path):
    instance = object.__new__(backup.Installation)
    instance.project = 'owndesk-' + 'a' * 12
    instance.state = tmp_path
    instance.marker = tmp_path / 'backup-recovery.json'
    backup.atomic(instance.marker, {'project': instance.project, 'containers': {
        'app': {'id': '--wrong', 'running': True}, 'db': {'id': 'b' * 64, 'running': False}}})
    with patch.object(backup, 'safe_file', return_value=instance.marker), patch.object(backup, 'Docker') as docker:
        instance.docker = docker.return_value
        # db is validated first, then malformed app id must reject without starting anything.
        instance.validate_container = lambda *args: None
        with pytest.raises(ValueError, match='identity'):
            instance.resume()
        instance.docker.run.assert_not_called()


@pytest.mark.skipif(os.name != 'posix', reason='Real flock requires Linux')
def test_overlapping_maintenance_is_rejected(tmp_path):
    with setup.operation_lock(tmp_path, create=True):
        with pytest.raises(RuntimeError, match='Another OwnDesk'):
            with setup.operation_lock(tmp_path):
                pytest.fail('Concurrent maintenance entered the lock')


@pytest.mark.skipif(os.name != 'posix', reason='Real no-follow lock requires Linux')
def test_lock_does_not_follow_links(tmp_path):
    state = tmp_path / '.runtime/linux'
    state.mkdir(parents=True, mode=0o700)
    target = tmp_path / 'untouched'
    target.write_text('original')
    (state / 'maintenance.lock').symlink_to(target)
    with pytest.raises((OSError, ValueError)):
        with setup.operation_lock(tmp_path):
            pytest.fail('A linked lock was accepted')
    assert target.read_text() == 'original'
