import importlib.util
import hashlib
from pathlib import Path
import sys
import xml.etree.ElementTree as ET
from unittest.mock import patch
from zipfile import ZipFile

import pytest

INFRA = Path(__file__).resolve().parents[1] / 'infra'
spec = importlib.util.spec_from_file_location('windows_setup', INFRA / 'windows_setup.py')
setup = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = setup
spec.loader.exec_module(setup)
spec_service = importlib.util.spec_from_file_location('windows_service', INFRA / 'windows_service.py')
service = importlib.util.module_from_spec(spec_service)
spec_service.loader.exec_module(service)


@pytest.mark.parametrize('executable,clean_paths', [
    ('powershell.exe', True),
    (r'C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe', True),
    ('pwsh.exe', False),
    ('python.exe', False),
])
def test_child_powershell_does_not_inherit_incompatible_module_paths(monkeypatch, executable, clean_paths):
    import os
    monkeypatch.setenv('PSModulePath', 'C:/Program Files/PowerShell/7/Modules')
    monkeypatch.setenv('OWNDESK_TEST_ENV', 'preserved')
    class Child:
        stdout = []
        def __enter__(self): return self
        def __exit__(self, *args): return False
        def wait(self): return 0
    with patch.object(setup.subprocess, 'Popen', return_value=Child()) as popen:
        setup.execute([executable, '-NoProfile'], lambda _: None)
    environment = popen.call_args.kwargs['env']
    assert any(key.upper() == 'PSMODULEPATH' for key in environment) is not clean_paths
    assert environment['OWNDESK_TEST_ENV'] == 'preserved'
    assert environment['PYTHONUTF8'] == '1'
    assert os.environ['PSModulePath'] == 'C:/Program Files/PowerShell/7/Modules'


def test_download_rejects_corruption_and_removes_partial_file(tmp_path):
    from io import BytesIO
    item = {'filename': 'tool.exe', 'url': 'https://example.test/tool.exe',
            'size': 4, 'sha256': hashlib.sha256(b'good').hexdigest()}
    target = tmp_path / 'tool.exe'
    target.write_bytes(b'evil')
    with patch.object(setup, 'urlopen', return_value=BytesIO(b'evil')):
        with pytest.raises(RuntimeError, match='SHA-256'):
            setup.download(item, tmp_path, lambda _: None)
    assert not target.with_suffix('.exe.partial').exists()
    with patch.object(setup, 'urlopen', return_value=BytesIO(b'good')):
        assert setup.download(item, tmp_path, lambda _: None).read_bytes() == b'good'
    with patch.object(setup, 'urlopen', side_effect=AssertionError('Valid cached file must not be downloaded')):
        setup.download(item, tmp_path)


@pytest.mark.parametrize('name', ['../escape.exe', '/escape.exe', 'pgsql/bin/../../escape.exe', 'pgsql/bin/C:evil', 'pgsql\\bin\\escape.exe'])
def test_archive_paths_cannot_escape_private_staging(tmp_path, name):
    archive = tmp_path / 'archive.zip'
    with ZipFile(archive, 'w') as package:
        package.writestr(name, b'bad')
    if '\\' in name:
        # ZipInfo normalizes Windows separators when writing; simulate a malformed downloaded ZIP.
        archive.write_bytes(archive.read_bytes().replace(name.replace('\\', '/').encode(), name.encode()))
    with pytest.raises(ValueError, match='Unsafe'):
        setup.extract(archive, tmp_path / 'staging', ('pgsql/bin/',))
    assert not (tmp_path / 'escape.exe').exists()


def test_only_postgres_runtime_is_extracted(tmp_path):
    archive = tmp_path / 'archive.zip'
    with ZipFile(archive, 'w') as package:
        package.writestr('pgsql/bin/postgres.exe', b'executable')
        package.writestr('pgsql/share/postgresql.conf.sample', b'config')
        package.writestr('pgsql/pgAdmin 4/ignored.exe', b'not required')
    target = tmp_path / 'staging'
    setup.extract(archive, target, ('pgsql/bin/', 'pgsql/share/'))
    assert (target / 'pgsql/bin/postgres.exe').is_file()
    assert not (target / 'pgsql/pgAdmin 4').exists()


def test_python_copy_preserves_venv_launchers_and_repairs_old_preview(tmp_path):
    source, target = tmp_path / 'source', tmp_path / 'installed'
    templates = source / 'Lib/venv/scripts/nt'
    templates.mkdir(parents=True)
    (templates / 'python.exe').write_bytes(b'venv launcher')
    (source / 'python.exe').write_bytes(b'interpreter')
    packages = source / 'Lib/site-packages'
    packages.mkdir()
    (packages / 'unrelated.py').write_bytes(b'user package')
    setup.copy_interpreter(source, target, lambda _: None)
    launcher = target / 'Lib/venv/scripts/nt/python.exe'
    assert launcher.read_bytes() == b'venv launcher'
    assert not (target / 'Lib/site-packages').exists()
    launcher.unlink()
    setup.copy_interpreter(source, target, lambda _: None)
    assert launcher.read_bytes() == b'venv launcher'


def test_service_uses_restricted_account_and_separate_stop_command(tmp_path):
    app, data = tmp_path / 'Program Files & code', tmp_path / 'ProgramData & private'
    xml = ET.fromstring(service.service_xml(app, data, app / 'runtime/venv/Scripts/python.exe'))
    assert xml.findtext('serviceaccount/domain') == 'NT AUTHORITY'
    assert xml.findtext('serviceaccount/user') == 'LocalService'
    assert xml.findtext('startmode') == 'Automatic'
    assert ' stop ' in xml.findtext('stoparguments')
    assert str(data) in xml.findtext('startarguments')
    assert xml.findtext('logpath') == str(data / 'service-logs')
    assert xml.find('onfailure').attrib == {'action': 'restart', 'delay': '30sec'}
    assert xml.find('serviceaccount/password') is None


def test_port_conflict_stops_before_installation_mutations():
    with patch.object(service, 'socket') as sockets:
        sockets.socket.return_value.__enter__.return_value.bind.side_effect = OSError('used')
        with pytest.raises(RuntimeError, match='5173'):
            service.check_ports()


def test_portal_service_has_separate_identity_and_can_be_staged_manually(tmp_path):
    xml = ET.fromstring(service.service_xml(tmp_path, tmp_path / 'data', tmp_path / 'python.exe',
        service_id='OwnDeskPortal', account='NetworkService', entrypoint='portal_runtime.py',
        display_name='OwnDesk Portal', start_mode='Manual'))
    assert xml.findtext('serviceaccount/user') == 'NetworkService'
    assert xml.findtext('id') == 'OwnDeskPortal'
    assert xml.findtext('startmode') == 'Manual'
    assert xml.findtext('delayedAutoStart') == 'false'
    assert 'portal_runtime.py' in xml.findtext('startarguments')


@pytest.mark.parametrize('options', [{'account': 'LocalSystem'}, {'entrypoint': '../other.py'},
    {'service_id': "unsafe';value"}, {'start_mode': 'Disabled'}])
def test_unsafe_service_configuration_is_rejected(tmp_path, options):
    with pytest.raises(ValueError):
        service.service_xml(tmp_path, tmp_path, tmp_path / 'python.exe', **options)


def test_old_active_status_does_not_open_an_unreachable_server(tmp_path):
    import json
    from uuid import uuid4
    identity = uuid4()
    (tmp_path / 'portal-connection.json').write_text(json.dumps({'id': str(identity)}))
    (tmp_path / 'runtime-profile.json').write_text(json.dumps({'public_hostname': 'org-' + identity.hex + '.owndesk.work'}))
    (tmp_path / 'portal-connection.json.status.json').write_text(json.dumps({'state': 'active', 'checked_at': 100}))
    with patch.object(service.time, 'time', return_value=101), patch.object(service.time, 'monotonic', side_effect=[0, 0, 10]), \
         patch.object(service.time, 'sleep'), patch.object(service, 'urlopen', side_effect=OSError('Server is stopped')):
        with pytest.raises(RuntimeError, match='публичный адрес'):
            service.wait_ready(tmp_path, tmp_path, timeout=5)


def test_public_link_must_match_the_paired_installation(tmp_path):
    import json
    from uuid import uuid4
    (tmp_path / 'portal-connection.json').write_text(json.dumps({'id': str(uuid4())}))
    (tmp_path / 'runtime-profile.json').write_text(json.dumps({'public_hostname': 'other.example'}))
    (tmp_path / 'portal-connection.json.status.json').write_text(json.dumps({'state': 'active', 'checked_at': 100}))
    with patch.object(service.time, 'time', return_value=101), patch.object(service, 'urlopen') as request:
        with pytest.raises(RuntimeError, match='не совпадает'):
            service.wait_ready(tmp_path, tmp_path, timeout=5)
        request.assert_not_called()


def test_readiness_identifies_the_installer_to_the_public_gateway(tmp_path):
    import io, json
    from uuid import uuid4
    identity = uuid4()
    origin = 'https://org-' + identity.hex + '.owndesk.work'
    (tmp_path / 'portal-connection.json').write_text(json.dumps({'id':str(identity)}))
    (tmp_path / 'runtime-profile.json').write_text(json.dumps({'public_hostname':origin.split('//')[1]}))
    (tmp_path / 'portal-connection.json.status.json').write_text(json.dumps({'state':'active','checked_at':100}))
    class Response(io.BytesIO):
        status = 200
        def geturl(self): return origin + '/api/v1/health/live'
    def gateway(request, timeout):
        assert request.get_header('User-agent').startswith('OwnDesk/')
        assert request.get_header('Authorization') is None
        assert request.full_url == origin + '/api/v1/health/live'
        return Response(b'{"status":"alive"}')
    with patch.object(service.time, 'time', return_value=101), patch.object(service, 'urlopen', side_effect=gateway):
        assert service.wait_ready(tmp_path, tmp_path, timeout=5) == origin + '/'
