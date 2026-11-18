import importlib.util
import json
from pathlib import Path
import sys
from unittest.mock import patch, MagicMock
from zipfile import ZipFile, ZipInfo

import pytest

INFRA = Path(__file__).resolve().parents[1] / 'infra'
sys.path.insert(0, str(INFRA))
spec = importlib.util.spec_from_file_location('windows_update', INFRA / 'windows_update.py')
update = importlib.util.module_from_spec(spec)
spec.loader.exec_module(update)
sys.modules[spec.name] = update


def release(tmp_path, extras=None):
    files = {name: b'test-only' for name in update.REQUIRED | set(update.RUNTIME_FILES)}
    files['VERSION'] = b'0.4.29\n'
    files.update(extras or {})
    archive = tmp_path / 'release.zip'
    with ZipFile(archive, 'w') as out:
        for name, body in files.items():
            entry = ZipInfo('placeholder')
            entry.filename = entry.orig_filename = 'OwnDesk/' + name
            out.writestr(entry, body)
    return archive, files


def test_release_requires_a_trusted_matching_checksum(tmp_path):
    archive, _ = release(tmp_path)
    with pytest.raises(ValueError, match='SHA-256'):
        update.archive_manifest(archive, '0' * 64)
    assert update.archive_manifest(archive, update.sha(archive))['version'] == '0.4.29'


@pytest.mark.parametrize('name', ['../foreign', '/absolute', 'folder/../foreign', 'folder\\foreign',
    'folder/file:stream', 'CON.txt', 'folder/NUL', 'folder/name.', 'folder/name ', '.env',
    '.runtime/secrets.json', 'private.key'])
def test_unsafe_archive_is_rejected_before_extraction(tmp_path, name):
    archive, _ = release(tmp_path, {name: b'unsafe'})
    with pytest.raises(ValueError):
        update.archive_manifest(archive, update.sha(archive))


def test_case_collisions_are_rejected_for_windows(tmp_path):
    archive, _ = release(tmp_path, {'backend/MANAGE.py': b'duplicate'})
    with pytest.raises(ValueError, match='Duplicate'):
        update.archive_manifest(archive, update.sha(archive))


def installed_app(tmp_path, files):
    app = tmp_path / 'installed'
    for name, content in files.items():
        target = app / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
    (app / 'VERSION').write_text('0.4.28')
    return app


def test_backend_and_runtime_changes_require_separate_review(tmp_path):
    archive, files = release(tmp_path)
    candidate = update.archive_manifest(archive, update.sha(archive))
    app = installed_app(tmp_path, files)
    assert update.compatibility(app, candidate) == '0.4.28'
    (app / 'backend/manage.py').write_text('different behavior')
    with pytest.raises(ValueError, match='application code changed'):
        update.compatibility(app, candidate)


def test_pinned_dependency_change_is_rejected(tmp_path):
    archive, files = release(tmp_path)
    candidate = update.archive_manifest(archive, update.sha(archive))
    app = installed_app(tmp_path, files)
    (app / 'infra/windows-python.json').write_text('different runtime')
    with pytest.raises(ValueError, match='dependencies changed'):
        update.compatibility(app, candidate)


def test_frontend_update_requires_explicit_opt_in(tmp_path):
    archive, files = release(tmp_path)
    candidate = update.archive_manifest(archive, update.sha(archive))
    app = installed_app(tmp_path, files)
    (app / 'frontend/dist/index.html').write_text('previous frontend')
    with pytest.raises(ValueError, match='application code changed'):
        update.compatibility(app, candidate)
    assert update.compatibility(app, candidate, frontend_only=True) == '0.4.28'
    # A package field cannot silently opt into the new mode.
    candidate['frontend_only'] = True
    with pytest.raises(ValueError, match='application code changed'):
        update.compatibility(app, candidate)


@pytest.mark.parametrize('relative', ['backend/manage.py', 'backend/new_migration.py',
    'backend/requirements.txt', *update.RUNTIME_FILES, *update.PIN_FILES])
def test_frontend_opt_in_never_allows_backend_runtime_or_dependency_changes(tmp_path, relative):
    archive, files = release(tmp_path)
    candidate = update.archive_manifest(archive, update.sha(archive))
    app = installed_app(tmp_path, files)
    path = app / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('unexpected change')
    with pytest.raises(ValueError):
        update.compatibility(app, candidate, frontend_only=True)


@pytest.mark.parametrize('value', ['true', 1, None])
def test_frontend_review_flag_does_not_accept_truthy_values(tmp_path, value):
    archive, files = release(tmp_path)
    candidate = update.archive_manifest(archive, update.sha(archive))
    app = installed_app(tmp_path, files)
    with pytest.raises(ValueError, match='explicit boolean'):
        update.compatibility(app, candidate, frontend_only=value)


def test_runtime_logic_change_is_rejected(tmp_path):
    archive, files = release(tmp_path)
    candidate = update.archive_manifest(archive, update.sha(archive))
    app = installed_app(tmp_path, files)
    (app / 'infra/native_runtime.py').write_text('different startup behavior')
    with pytest.raises(ValueError, match='runtime behavior changed'):
        update.compatibility(app, candidate)


def transaction(tmp_path):
    app, data = tmp_path / 'installed', tmp_path / 'live-data'
    app.mkdir()
    data.mkdir()
    spec = update.Spec('OwnDeskServer', app / 'OwnDeskService.exe', data, 'LocalService')
    xml = update.service.service_xml(app, data, app / 'runtime/venv/Scripts/python.exe')
    spec.xml.write_text(xml, encoding='utf-8')
    slot = tmp_path / 'transaction'
    slot.mkdir()
    record = dict(phase='prepared', service=spec.name, old_app=str(app), new_app=str(tmp_path / 'new-app'),
        old_xml=xml, old_version='0.4.28', new_version='0.4.29')
    return spec, slot, record


@pytest.mark.parametrize('update_kind', ['maintenance', 'frontend-only'])
def test_failed_health_rolls_back_code_while_retaining_new_data(tmp_path, update_kind):
    spec, slot, record = transaction(tmp_path)
    record['update_kind'] = update_kind
    live = spec.data / 'accepted-writes'
    live.write_text('before')
    def fail_after_new_write(_):
        live.write_text('before\nafter-switch')
        raise RuntimeError('intentional acceptance failure')
    with patch.object(update, 'installed', return_value=(spec.host.parent, record['old_xml'])), \
            patch.object(update, 'query', return_value={'State': 'Running'}), patch.object(update, 'stop'), \
            patch.object(update.service, 'secure_directory'), patch.object(update.service, 'execute'), \
            patch('native_backup.backup') as backup, patch.object(update, 'health'), \
            pytest.raises(RuntimeError, match='previous code restarted'):
        update.apply(spec, slot, record, tmp_path / 'backup', check=fail_after_new_write)
    assert spec.xml.read_text(encoding='utf-8') == record['old_xml']
    assert live.read_text() == 'before\nafter-switch'
    assert json.loads((slot / 'update.json').read_text())['phase'] == 'rolled-back'
    backup.assert_called_once()


def test_backup_failure_never_stops_running_service(tmp_path):
    spec, slot, record = transaction(tmp_path)
    with patch.object(update, 'installed', return_value=(spec.host.parent, record['old_xml'])), \
            patch.object(update, 'query', return_value={'State': 'Running'}), patch.object(update, 'stop') as stop, \
            patch.object(update.service, 'secure_directory'), patch('native_backup.backup', side_effect=RuntimeError('failed')), \
            pytest.raises(RuntimeError, match='failed'):
        update.apply(spec, slot, record, tmp_path / 'backup')
    stop.assert_not_called()
    assert spec.xml.read_text(encoding='utf-8') == record['old_xml']


def test_recovery_refuses_configuration_changed_by_another_operator(tmp_path):
    spec, slot, record = transaction(tmp_path)
    spec.xml.write_text('<changed-by-another-operator/>')
    with patch.object(update, 'stop') as stop, pytest.raises(RuntimeError, match='outside this transaction'):
        update.rollback(spec, slot, record)
    stop.assert_not_called()


def test_public_service_requires_both_local_and_external_health(tmp_path):
    data = tmp_path / 'data'
    data.mkdir()
    (data / 'runtime-profile.json').write_text(json.dumps({'public_hostname': 'owndesk.work', 'origin_port': 5185}))
    spec = update.Spec('OwnDeskPortal', tmp_path / 'host.exe', data, 'NetworkService')
    local = MagicMock()
    public = MagicMock()
    for value, address in ((local, 'http://127.0.0.1:5185/api/v1/health/live'),
                           (public, 'https://owndesk.work/api/v1/health/live')):
        value.__enter__.return_value = value
        value.status = 200
        value.geturl.return_value = address
        value.read.return_value = b'{"status":"alive"}'
    with patch.object(update, 'query', return_value={'State': 'Running'}), \
            patch.object(update, 'urlopen', side_effect=[local, public]) as request:
        update.health(spec, timeout=1)
    assert request.call_count == 2
    assert request.call_args_list[1].args[0].full_url == 'https://owndesk.work/api/v1/health/live'


def security_release(tmp_path, changes=None):
    original = b'Django==5.2.19\nPyJWT==2.10.1\n'
    replacements = {'backend/requirements.txt':original.replace(b'2.10.1',b'2.15.1'),
                    'backend/security-sentinel.py':b'unchanged backend',
                    'frontend/dist/assets/security-sentinel.js':b'unchanged frontend'}
    replacements.update(changes or {})
    archive,files = release(tmp_path,replacements)
    old=dict(files)
    old['backend/requirements.txt']=original
    old['backend/security-sentinel.py']=b'unchanged backend'
    old['frontend/dist/assets/security-sentinel.js']=b'unchanged frontend'
    app=installed_app(tmp_path,old)
    return app,update.archive_manifest(archive,update.sha(archive))


def test_pyjwt_security_refresh_requires_explicit_reviewed_mode(tmp_path):
    app,candidate=security_release(tmp_path)
    with pytest.raises(ValueError,match='application code changed'):
        update.compatibility(app,candidate)
    with pytest.raises(ValueError,match='application code changed'):
        update.compatibility(app,candidate,frontend_only=True)
    assert update.compatibility(app,candidate,refresh_pyjwt=True)=='0.4.28'


@pytest.mark.parametrize('change',[
    {'backend/requirements.txt':b'Django==5.2.20\nPyJWT==2.15.1\n'},
    {'backend/requirements.txt':b'Django==5.2.19\nPyJWT==2.15.0\n'},
    {'backend/security-sentinel.py':b'changed backend'},
    {'frontend/dist/assets/security-sentinel.js':b'changed frontend'},
    {'infra/native_runtime.py':b'changed runtime'},
    {'infra/windows-python.json':b'changed interpreter'},
])
def test_pyjwt_refresh_rejects_unreviewed_changes(tmp_path,change):
    app,candidate=security_release(tmp_path,change)
    # Preserve original runtime/interpreter files on the installed side too.
    for name in ('infra/native_runtime.py','infra/windows-python.json'):
        (app/name).write_bytes(b'test-only')
    with pytest.raises(ValueError):
        update.compatibility(app,candidate,refresh_pyjwt=True)


@pytest.mark.parametrize('flag',[1,'true',None,{},[]])
def test_pyjwt_refresh_rejects_ambiguous_flags(tmp_path,flag):
    app,candidate=security_release(tmp_path)
    with pytest.raises(ValueError,match='must be explicit'):
        update.compatibility(app,candidate,refresh_pyjwt=flag)


def test_pyjwt_refresh_cannot_include_frontend_mode(tmp_path):
    app,candidate=security_release(tmp_path)
    with pytest.raises(ValueError,match='cannot include frontend'):
        update.compatibility(app,candidate,refresh_pyjwt=True,frontend_only=True)


def test_pyjwt_refresh_requires_exact_old_pin(tmp_path):
    app,candidate=security_release(tmp_path)
    (app/'backend/requirements.txt').write_text('Django==5.2.19\nPyJWT==2.10.10\n')
    with pytest.raises(ValueError,match='source pin'):
        update.compatibility(app,candidate,refresh_pyjwt=True)
