"""Restore a backup into a quarantined Windows database; never publish or replace a live service."""
import argparse
import base64
from contextlib import ExitStack
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import socket
import subprocess
import sys
import time
from uuid import uuid4
import xml.etree.ElementTree as ET

from native_backup import verify, safe_file, fingerprint, CONFIG_FILES
from native_runtime import ROOT, save_private


def progress(message):
    print(json.dumps({'event': 'progress', 'message': message}, ensure_ascii=False), flush=True)


def inspect_backup(directory):
    manifest = verify(directory)
    if not isinstance(manifest.get('version'), str) or not re.fullmatch(r'[0-9]+\.[0-9]+\.[0-9]+', manifest['version']):
        raise ValueError('Unsupported backup version metadata')
    counts = manifest.get('counts')
    if not isinstance(counts, dict) or not 1 <= len(counts) <= 128 or any(
        not re.fullmatch(r'[a-z][a-z0-9_]{0,62}', key) or type(value) is not int or not 0 <= value <= 10**19
        for key, value in counts.items()):
        raise ValueError('Invalid recovery table counts')
    for name in manifest['files']:
        if name != 'database.dump' and name not in {'config/' + name for name in CONFIG_FILES} and not name.startswith('private-attachments/'):
            raise ValueError('Unexpected file in recovery manifest')
    ready = manifest.get('ready_attachments')
    media = [name for name in manifest['files'] if name.startswith('private-attachments/')]
    if type(ready) is not int or ready < len(media):
        raise ValueError('Invalid attachment count in backup')
    original = json.loads(safe_file(directory, 'config/secrets.json').read_text())
    keys = ('SECRET_KEY', 'ACCOUNT_ENCRYPTION_KEY')
    if manifest['kind'] == 'organization': keys += ('TOKEN_ENCRYPTION_KEY',)
    if any(not isinstance(original.get(key), str) or not original[key] for key in keys):
        raise ValueError('Original recovery keys are missing')
    for key in keys:
        if key.endswith('ENCRYPTION_KEY'):
            try: value = base64.b64decode(original[key], altchars=b'-_', validate=True)
            except (ValueError, TypeError): raise ValueError('Invalid recovery encryption key') from None
            if len(value) != 32: raise ValueError('Invalid recovery encryption key')
    return manifest


def stage(directory, destination, binaries, port, expected_manifest=None):
    """Caller creates a private empty destination; recheck every copied file."""
    original_hash = fingerprint(safe_file(directory, 'manifest.json'))['sha256']
    if expected_manifest is not None and original_hash != expected_manifest:
        raise ValueError('Backup selection changed; verify it again')
    manifest = inspect_backup(directory)
    if not destination.is_dir() or any(destination.iterdir()):
        raise ValueError('Recovery requires a new private empty destination')
    if destination.resolve().is_relative_to(directory.resolve()) or directory.resolve().is_relative_to(destination.resolve()):
        raise ValueError('Recovery destination must be separate from the backup')
    if type(port) is not int or not 15000 <= port <= 65000 or port in {55485, 55486}:
        raise ValueError('Invalid isolated recovery port')
    probe = destination / 'probe'
    probe.mkdir()
    original_dir = destination / 'original-config'
    original_dir.mkdir()
    for name, expected in manifest['files'].items():
        if name == 'database.dump': target = probe / 'initial-restore.dump'
        elif name.startswith('config/'): target = original_dir / name.removeprefix('config/')
        else: target = probe / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(safe_file(directory, name), target)
        if fingerprint(target) != expected:
            raise ValueError('Backup changed during recovery preparation')
    original = json.loads((original_dir / 'secrets.json').read_text())
    keys = {key: value for key, value in original.items() if key in {'SECRET_KEY', 'ACCOUNT_ENCRYPTION_KEY', 'TOKEN_ENCRYPTION_KEY'}}
    # The recovered database gets new local credentials. Original DB passwords stay admin-only.
    secret = {**keys, 'POSTGRES_ADMIN_PASSWORD': secrets.token_urlsafe(32), 'POSTGRES_APP_PASSWORD': secrets.token_urlsafe(32)}
    save_private(probe / 'secrets.json', secret)
    profile = {'format': 1, 'quarantine': True, 'kind': manifest['kind'], 'version': manifest['version'],
        'postgres': {'bin': str(binaries), 'port': port, 'database': 'owndesk_recovery',
                     'role': 'recovery_app', 'admin_role': 'recovery_dba'},
        'initial_restore': {'file': 'initial-restore.dump', 'sha256': manifest['files']['database.dump']['sha256'], 'counts': manifest['counts']},
        'media': {name: value for name, value in manifest['files'].items() if name.startswith('private-attachments/')},
        'ready_attachments': manifest['ready_attachments'],
        'key_hashes': {key: hashlib.sha256(value.encode()).hexdigest() for key, value in keys.items()}}
    save_private(probe / 'probe-profile.json', profile)
    if fingerprint(safe_file(directory, 'manifest.json'))['sha256'] != original_hash:
        raise ValueError('Backup selection changed during recovery preparation')
    save_private(destination / 'backup-manifest.json', manifest)
    save_private(destination / 'recovery.json', {'format': 1, 'state': 'prepared', 'kind': manifest['kind'],
        'version': manifest['version'], 'created_at': time.time(), 'port': port})
    return manifest


def worker(data, stop=False):
    if stop:
        (data / 'probe-stop').touch()
        return
    from local_snapshot import local_runtime_lock
    from native_runtime import managed_database
    from native_restore import initial_restore
    for path in (data, *data.parents, data / 'pgdata'):
        if path.is_symlink() or path.is_junction(): raise ValueError('Quarantine data must not use links')
    profile = json.loads((data / 'probe-profile.json').read_text())
    pg = profile['postgres']
    if (profile.get('quarantine') is not True or pg['database'] != 'owndesk_recovery'
            or pg['role'] != 'recovery_app' or pg['admin_role'] != 'recovery_dba'
            or type(pg['port']) is not int or not 15000 <= pg['port'] <= 65000 or pg['port'] in {55485, 55486}):
        raise ValueError('Invalid quarantine profile')
    # No runtime profile, connector, SMTP configuration or tunnel token is ever installed here.
    for name in ('runtime-profile.json', 'portal-connection.json', 'integrations.json', 'cloudflare.json', 'root-tunnel.token'):
        if (data / name).exists(): raise ValueError('External runtime configuration is forbidden in quarantine')
    secret = json.loads((data / 'secrets.json').read_text())
    env = {key: os.environ[key] for key in ('SYSTEMROOT', 'WINDIR', 'COMSPEC', 'USERPROFILE',
        'LOCALAPPDATA', 'APPDATA', 'USERNAME', 'USERDOMAIN') if key in os.environ}
    temporary = data / 'temporary'
    temporary.mkdir(exist_ok=True)
    env.update(PATH=str(pg['bin']) + os.pathsep + os.environ.get('PATH', ''), PSYCOPG_IMPL='python',
               PYTHONUTF8='1', PYTHONDONTWRITEBYTECODE='1', TEMP=str(temporary), TMP=str(temporary), LANG='C', LC_ALL='C')
    # Services may have invalid inherited standard handles. Give database utilities explicit private handles.
    import native_runtime
    def private_command(args, environment, cwd=ROOT):
        with (data / 'native-command.log').open('ab') as log:
            completed = subprocess.run([str(value) for value in args], env=environment, cwd=cwd,
                stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
        if completed.returncode: raise RuntimeError('Recovery database utility failed; inspect native-command.log')
    native_runtime.command = private_command
    with local_runtime_lock(data / 'recovery-worker'), ExitStack() as stack:
        (data / 'probe-stop').unlink(missing_ok=True)
        process = managed_database(stack, data, pg, secret, env)
        initial_restore(data, profile, secret, env)
        import psycopg
        from psycopg import sql
        with psycopg.connect(host='127.0.0.1', port=pg['port'], dbname=pg['database'], user=pg['role'],
            password=secret['POSTGRES_APP_PASSWORD'], autocommit=True) as connection:
            for table, expected in profile['initial_restore']['counts'].items():
                count = connection.execute(sql.SQL('SELECT count(*) FROM {}').format(sql.Identifier(table))).fetchone()[0]
                if count != expected: raise RuntimeError('Recovered row count differs: ' + table)
            rows = connection.execute("SELECT object_key,size FROM attachments_attachment WHERE state='ready'").fetchall()
            if len(rows) != profile['ready_attachments']: raise RuntimeError('Recovered attachment count differs')
            if {'private-attachments/' + key for key, size in rows} != set(profile['media']):
                raise RuntimeError('Recovered file list differs from the database')
            for key, size in rows:
                name = 'private-attachments/' + key
                actual = fingerprint(safe_file(data, name))
                if actual != profile['media'][name] or actual['size'] != size:
                    raise RuntimeError('Recovered attachment differs')
        if any(hashlib.sha256(secret[key].encode()).hexdigest() != digest for key, digest in profile['key_hashes'].items()):
            raise RuntimeError('Recovered encryption keys differ')
        try:
            with (data.parent / 'original-config/secrets.json').open('rb'): pass
        except PermissionError:
            pass
        else:
            raise RuntimeError('The temporary worker can read admin-only original credentials')
        save_private(data / 'probe-result.json', {'status': 'verified', 'checked_at': time.time(),
            'tables': len(profile['initial_restore']['counts']), 'attachments': len(rows), 'keys_preserved': True,
            'quarantine': True, 'external_services_started': False, 'original_config_protected': True})
        deadline = time.monotonic() + 300
        while not (data / 'probe-stop').exists() and time.monotonic() < deadline:
            if process.poll() is not None: raise RuntimeError('Recovery database stopped unexpectedly')
            time.sleep(.5)


def recover(directory, expected_manifest=None):
    import windows_service as service
    from local_snapshot import local_runtime_lock
    service.admin_required()
    control = Path(os.environ['ProgramData']) / 'OwnDesk/RecoveryControl'
    service.secure_directory(control, admin_only=True)
    with local_runtime_lock(control / 'restore'):
        return _recover(directory, expected_manifest)


def _recover(directory, expected_manifest=None):
    import windows_service as service
    from windows_update import no_links, Spec, installed, query
    service.admin_required()
    progress('Проверяем копию, файлы и исходные ключи…')
    manifest = inspect_backup(directory)
    selected_hash = fingerprint(safe_file(directory, 'manifest.json'))['sha256']
    if expected_manifest is not None and expected_manifest != selected_hash:
        raise ValueError('Backup selection changed; verify it again')
    identity = uuid4().hex
    account = 'NetworkService' if manifest['kind'] == 'portal' else 'LocalService'
    sid = 'S-1-5-20' if manifest['kind'] == 'portal' else 'S-1-5-19'
    root = Path(os.environ['ProgramData']) / 'OwnDesk/Recovery'
    no_links(root)
    service.secure_directory(root, admin_only=True)
    destination = root / identity
    service.secure_directory(destination, admin_only=True)
    code = Path(os.environ['ProgramFiles']) / 'OwnDesk/RecoveryTools' / identity
    service.secure_directory(code.parent, shared_parent=True)
    service.secure_directory(code, service_sid=sid)
    name = 'Portal' if manifest['kind'] == 'portal' else 'Server'
    live = Spec('OwnDesk' + name, Path(os.environ['ProgramFiles']) / 'OwnDesk' / name / 'OwnDeskService.exe',
                Path(os.environ['ProgramData']) / 'OwnDesk' / name, account)
    try:
        app, _ = installed(live)
        profile = json.loads((live.data / 'runtime-profile.json').read_text())
        binaries = Path(profile['postgres']['bin'])
        python = app / 'runtime/venv/Scripts/python.exe'
        winsw = live.host
        pins = json.loads((ROOT / 'infra/windows-dependencies.json').read_text())
        if fingerprint(winsw)['sha256'] != pins['winsw']['sha256'] or not python.is_file():
            raise ValueError('Reusable runtime was not confirmed')
        progress('Используем готовую среду. Рабочая служба не останавливается…')
        (code / 'infra').mkdir()
        for file in ('windows_recovery.py', 'native_runtime.py', 'native_restore.py', 'native_backup.py'):
            shutil.copyfile(ROOT / 'infra' / file, code / 'infra' / file)
        (code / 'backend').mkdir()
        shutil.copyfile(ROOT / 'backend/local_snapshot.py', code / 'backend/local_snapshot.py')
        shutil.copyfile(ROOT / 'VERSION', code / 'VERSION')
    except (OSError, ValueError, KeyError):
        progress('Подготавливаем отдельную среду PostgreSQL и Python…')
        # No service was stopped; the standalone environment is private to this recovery.
        if any(code.iterdir()): raise RuntimeError('Partial recovery environment exists; inspect it before retrying')
        python, tools, _ = service.prepare_application(code, destination / 'environment', lambda _: None,
            service_id='OwnDeskRecovery' + identity, service_sid=sid)
        binaries, winsw = tools['postgres_bin'], tools['winsw']
        service.secure_directory(destination, admin_only=True)
        service.secure_directory(destination / 'environment', admin_only=True)
    for port in range(15590, 15630):
        try:
            with socket.socket() as probe: probe.bind(('127.0.0.1', port))
            break
        except OSError: continue
    else: raise RuntimeError('No isolated recovery port is available')
    progress('Восстанавливаем файлы в новый закрытый каталог…')
    # Environment preparation may already have left a private log directory here.
    staging = destination / 'staged'
    service.secure_directory(staging, admin_only=True)
    stage(directory, staging, binaries, port, selected_hash)
    progress('Отдельный каталог восстановления: ' + str(staging))
    probe = staging / 'probe'
    service.secure_directory(probe, writable=True, service_sid=sid)
    service.execute(['icacls', str(probe / '*'), '/reset', '/T', '/Q'], lambda _: None)
    for parent in (root, destination, staging):
        service.execute(['powershell.exe', '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass',
            '-File', ROOT / 'infra/set_recovery_traverse.ps1', '-Target', parent, '-ServiceSid', sid], lambda _: None)
    service.execute(['icacls', code, '/reset', '/T', '/Q'], lambda _: None)
    service.secure_directory(code, service_sid=sid)
    host = code / 'OwnDeskRecovery.exe'
    shutil.copyfile(winsw, host)
    service_name = 'OwnDeskRecovery' + identity
    xml = ET.fromstring(service.service_xml(code, probe, python, service_id=service_name,
        account=account, start_mode='Manual', display_name='OwnDesk isolated recovery verification'))
    # A verification worker must never retry forever if the controlling window disappears.
    for rule in xml.findall('onfailure'): xml.remove(rule)
    script = code / 'infra/windows_recovery.py'
    for key, action in (('startarguments', 'worker'), ('stoparguments', 'stop-worker')):
        xml.find(key).text = subprocess.list2cmdline([str(script), action, '--data', str(probe)])
    host.with_suffix('.xml').write_text(ET.tostring(xml, encoding='unicode'), encoding='utf-8')
    registered = False
    result = None
    try:
        service.execute([host, 'install'], lambda _: None)
        registered = True
        query(Spec(service_name, host, probe, account))
        progress('Импортируем базу в изолированный PostgreSQL. Интеграции отключены…')
        service.execute([host, 'start'], lambda _: None)
        deadline = time.monotonic() + 240
        while time.monotonic() < deadline:
            path = probe / 'probe-result.json'
            if path.exists():
                result = json.loads(path.read_text())
                if result.get('status') == 'verified': break
            if query(Spec(service_name, host, probe, account))['State'] == 'Stopped':
                raise RuntimeError('Recovery worker stopped; inspect logs in ' + str(probe))
            time.sleep(.5)
        else: raise RuntimeError('Recovery verification timed out; inspect the protected recovery logs')
    finally:
        if registered:
            recovery_spec = Spec(service_name, host, probe, account)
            if query(recovery_spec)['State'] != 'Stopped':
                service.execute([host, 'stopwait'], lambda _: None)
            if query(recovery_spec)['State'] != 'Stopped':
                raise RuntimeError('Isolated recovery service did not stop: ' + service_name)
            service.execute([host, 'uninstall'], lambda _: None)
        with socket.socket() as check: check.bind(('127.0.0.1', port))
        progress('Останавливаем проверку и закрываем служебный доступ к восстановленной копии…')
        service.secure_directory(probe, admin_only=True)
        service.execute(['icacls', probe, '/setowner', '*S-1-5-32-544', '/T', '/Q'], lambda _: None)
        service.execute(['icacls', str(probe / '*'), '/reset', '/T', '/Q'], lambda _: None)
        for parent in (staging, destination, root): service.secure_directory(parent, admin_only=True)
    with socket.socket() as check: check.bind(('127.0.0.1', port))
    if result is None or result.get('status') != 'verified': raise RuntimeError('Recovery was not verified')
    result.update(recovery=identity, directory=str(staging), kind=manifest['kind'], version=manifest['version'],
                  temporary_service_removed=True, database_stopped=True)
    save_private(staging / 'recovery.json', result)
    progress('Копия восстановлена и проверена. Тестовая база остановлена, рабочая установка не изменена.')
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=('verify', 'recover', 'worker', 'stop-worker'))
    parser.add_argument('--backup', type=Path)
    parser.add_argument('--data', type=Path)
    parser.add_argument('--manifest-sha256')
    args = parser.parse_args()
    if args.action in {'worker', 'stop-worker'}:
        if args.data is None: parser.error('--data is required')
        worker(args.data.resolve(), stop=args.action == 'stop-worker')
        return
    import windows_service as service
    from windows_update import no_links
    service.admin_required()
    if args.backup is None: parser.error('--backup is required')
    no_links(args.backup)
    if args.action == 'verify':
        progress('Проверяем файлы, контрольные суммы и метаданные копии…')
        manifest = inspect_backup(args.backup)
        result = {'status': 'backup-verified', 'kind': manifest['kind'], 'version': manifest['version'],
                  'files': len(manifest['files']), 'attachments': manifest['ready_attachments'],
                  'manifest_sha256': fingerprint(safe_file(args.backup, 'manifest.json'))['sha256']}
    else:
        result = recover(args.backup, args.manifest_sha256)
    print(json.dumps(result), flush=True)


if __name__ == '__main__':
    try: main()
    except Exception as error:
        print('OwnDesk recovery: ' + str(error), file=sys.stderr)
        raise SystemExit(1)
