"""Windows maintenance and explicitly reviewed frontend updates; never roll back the live database."""
import argparse
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
import sys
import time
from urllib.request import Request, urlopen
import xml.etree.ElementTree as ET
from zipfile import ZipFile

import windows_service as service
import windows_setup as setup
from native_runtime import save_private

MAX_EXPANDED = 128 * 1024 * 1024
REQUIRED = {'VERSION', 'backend/manage.py', 'backend/requirements.txt', 'frontend/dist/index.html',
            'infra/native_runtime.py', 'infra/portal_runtime.py', 'infra/windows-dependencies.json',
            'infra/windows-python.json'}
PIN_FILES = ('infra/windows-dependencies.json', 'infra/windows-python.json')
RUNTIME_FILES = ('infra/native_runtime.py', 'infra/portal_runtime.py', 'infra/native_restore.py',
                 'infra/Caddyfile.connector', 'infra/Caddyfile.portal-native')
_PROGRESS = False


def progress(message):
    if _PROGRESS:
        print(json.dumps({'event': 'progress', 'message': message}, ensure_ascii=False), flush=True)


def sha(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def version(value):
    if not re.fullmatch(r'[0-9]+\.[0-9]+\.[0-9]+', value):
        raise ValueError('Invalid release version')
    return tuple(map(int, value.split('.')))


def no_links(path):
    for candidate in (path, *path.parents):
        if candidate.is_symlink() or candidate.is_junction():
            raise ValueError('Update paths must not use links')


def archive_manifest(archive, expected_sha):
    if not re.fullmatch(r'[a-f0-9]{64}', expected_sha) or sha(archive) != expected_sha:
        raise ValueError('Archive SHA-256 does not match the trusted release metadata')
    files, seen = {}, set()
    with ZipFile(archive) as package:
        entries = package.infolist()
        if len(entries) > 5000 or sum(item.file_size for item in entries) > MAX_EXPANDED:
            raise ValueError('Archive exceeds update limits')
        for item in entries:
            raw = item.orig_filename.rstrip('/') if item.is_dir() else item.orig_filename
            name = PurePosixPath(raw)
            if (not raw or '\\' in raw or ':' in raw or '\x00' in raw or name.is_absolute()
                    or name.as_posix() != raw or '..' in name.parts or name.parts[0] != 'OwnDesk'):
                raise ValueError('Unsafe release archive path')
            for part in name.parts:
                if (part.endswith(('.', ' ')) or re.fullmatch(r'(con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\..*)?', part, re.I)):
                    raise ValueError('Unsafe Windows archive name')
            if raw.casefold() in seen or (item.external_attr >> 16) & 0o170000 not in {0, 0o100000, 0o040000}:
                raise ValueError('Duplicate name or non-regular archive entry')
            seen.add(raw.casefold())
            if item.is_dir():
                continue
            if len(name.parts) < 2:
                raise ValueError('Invalid release root')
            relative = PurePosixPath(*name.parts[1:]).as_posix()
            if (any(part in {'.runtime', '.git', '__pycache__', 'node_modules'} for part in name.parts)
                    or (name.name.startswith('.env') and name.name != '.env.example')
                    or name.suffix.lower() in {'.key', '.pem', '.sqlite3', '.pyc', '.log'}):
                raise ValueError('Release contains private or generated files')
            with package.open(item) as stream:
                files[relative] = hashlib.file_digest(stream, 'sha256').hexdigest()
        if not REQUIRED <= files.keys():
            raise ValueError('Incomplete OwnDesk release')
        names = {name.casefold() for name in files}
        if any(parent.as_posix().casefold() in names for name in files for parent in PurePosixPath(name).parents if str(parent) != '.'):
            raise ValueError('File and directory collision in release')
        release = package.read('OwnDesk/VERSION').decode('utf-8').strip()
    version(release)
    return {'version': release, 'sha256': expected_sha, 'files': files}


def tree_manifest(app, prefix):
    result = {}
    for path in (app / prefix).rglob('*'):
        if path.is_symlink() or path.is_junction():
            raise ValueError('Installed code contains links')
        if path.is_file() and not {'__pycache__', '.pytest_cache', '.ruff_cache'} & set(path.parts) and path.suffix != '.pyc':
            result[path.relative_to(app).as_posix()] = sha(path)
    return result


def compatibility(app, candidate, *, frontend_only=False, refresh_pyjwt=False):
    if type(frontend_only) is not bool:
        raise ValueError('frontend_only must be an explicit boolean')
    if type(refresh_pyjwt) is not bool or (refresh_pyjwt and frontend_only):
        raise ValueError('Dependency refresh must be explicit and cannot include frontend changes')
    no_links(app)
    if refresh_pyjwt:
        # Reviewed security transition only. No arbitrary requirement, application,
        # schema, proxy, interpreter or frontend changes are permitted by this flag.
        previous = (app / 'backend/requirements.txt').read_bytes()
        if previous.count(b'PyJWT==2.10.1') != 1 or previous.splitlines().count(b'PyJWT==2.10.1') != 1:
            raise ValueError('The reviewed PyJWT source pin was not found')
        expected = previous.replace(b'PyJWT==2.10.1', b'PyJWT==2.15.1')
        if hashlib.sha256(expected).hexdigest() != candidate['files'].get('backend/requirements.txt'):
            raise ValueError('Dependency refresh permits only the reviewed PyJWT pin change')
    current = (app / 'VERSION').read_text().strip()
    if version(candidate['version']) <= version(current):
        raise ValueError('Update must be newer than the installed release')
    for prefix in ('backend', 'frontend/dist'):
        if prefix == 'frontend/dist' and frontend_only:
            continue
        proposed = {name: digest for name, digest in candidate['files'].items() if name.startswith(prefix + '/')}
        existing = tree_manifest(app, prefix)
        if refresh_pyjwt and prefix == 'backend':
            existing.pop('backend/requirements.txt', None)
            proposed.pop('backend/requirements.txt', None)
        if existing != proposed:
            raise ValueError('Maintenance update refused: application code changed; a migration/recovery review is required')
    for relative in PIN_FILES:
        if sha(app / relative) != candidate['files'].get(relative):
            raise ValueError('Maintenance update refused: pinned runtime dependencies changed')
    for relative in RUNTIME_FILES:
        if not (app / relative).is_file() or sha(app / relative) != candidate['files'].get(relative):
            raise ValueError('Maintenance update refused: database/runtime behavior changed')
    return current


@dataclass
class Spec:
    name: str
    host: Path
    data: Path
    account: str

    @property
    def sid(self):
        return {'LocalService': 'S-1-5-19', 'NetworkService': 'S-1-5-20'}[self.account]

    @property
    def xml(self):
        return self.host.with_suffix('.xml')


def query(spec):
    if not re.fullmatch(r'OwnDesk[A-Za-z0-9]{1,60}', spec.name):
        raise ValueError('Invalid OwnDesk service name')
    result = subprocess.run(['powershell.exe', '-NoProfile', '-NonInteractive', '-Command',
        f"Get-CimInstance Win32_Service -Filter \"Name='{spec.name}'\" | Select-Object Name,State,StartName,PathName | ConvertTo-Json -Compress"],
        capture_output=True, text=True, check=True, creationflags=subprocess.CREATE_NO_WINDOW,
        env=setup.windows_powershell_environment())
    value = json.loads(result.stdout) if result.stdout.strip() else None
    if not value or value['StartName'].lower() != 'nt authority\\' + spec.account.lower():
        raise RuntimeError('The registered service identity was not confirmed')
    if value['PathName'] not in {str(spec.host), '"' + str(spec.host) + '"'}:
        raise RuntimeError('Unexpected registered service executable')
    return value


def installed(spec):
    no_links(spec.host)
    no_links(spec.data)
    text = spec.xml.read_text(encoding='utf-8')
    if len(text) > 32768:
        raise ValueError('Unexpected service configuration')
    xml = ET.fromstring(text)
    app = Path(xml.findtext('workingdirectory', ''))
    no_links(app)
    python = app / 'runtime/venv/Scripts/python.exe'
    entrypoint = 'portal_runtime.py' if spec.account == 'NetworkService' else 'native_runtime.py'
    expected_start = subprocess.list2cmdline([str(app / 'infra' / entrypoint), 'run', '--data', str(spec.data)])
    expected_stop = subprocess.list2cmdline([str(app / 'infra' / entrypoint), 'stop', '--data', str(spec.data)])
    if (xml.findtext('id') != spec.name or xml.findtext('executable') != str(python)
            or xml.findtext('startarguments') != expected_start or xml.findtext('stoparguments') != expected_stop
            or xml.findtext('serviceaccount/user') != spec.account
            or xml.findtext('serviceaccount/domain') != 'NT AUTHORITY'):
        raise ValueError('Unsupported service configuration; automatic update was not started')
    return app, text


def packages(python):
    code = "import importlib.metadata as m,json; print(json.dumps({d.metadata['Name'].lower():d.version for d in m.distributions()}))"
    result = subprocess.run([str(python), '-B', '-c', code], capture_output=True, text=True,
        check=True, creationflags=subprocess.CREATE_NO_WINDOW)
    return json.loads(result.stdout)


def prepare(spec, archive, candidate, slot, cache=None, *, frontend_only=False, refresh_pyjwt=False):
    progress('Подтверждаем службу и проверяем установленную программу…')
    service.admin_required()
    if query(spec)['State'] != 'Running':
        raise RuntimeError('Start the existing service before preparing an update')
    app, old_xml = installed(spec)
    if frontend_only and spec.account != 'NetworkService':
        raise ValueError('Frontend-only updates currently require the central portal')
    if refresh_pyjwt and spec.account != 'NetworkService':
        raise ValueError('Reviewed dependency refresh currently requires the central portal')
    old_version = compatibility(app, candidate, frontend_only=frontend_only, refresh_pyjwt=refresh_pyjwt)
    service.secure_directory(slot, admin_only=True)
    source = slot / 'source'
    progress('Распаковываем проверенный архив в отдельный каталог…')
    source.mkdir()
    # Archive was fully checked before any file was extracted or executed.
    if sha(archive) != candidate['sha256']:
        raise ValueError('Archive changed after validation')
    with ZipFile(archive) as package:
        for relative in candidate['files']:
            path = source / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            with package.open('OwnDesk/' + relative) as stream, path.open('xb') as destination:
                while chunk := stream.read(1024 * 1024):
                    destination.write(chunk)
            if sha(path) != candidate['files'][relative]:
                raise ValueError('Extracted release file differs')
    target = spec.host.parent.parent / (spec.host.parent.name + 'Releases') / (candidate['version'] + '-' + candidate['sha256'][:12])
    prepare_data = slot / 'prepare-data'
    installed_packages = packages(app / 'runtime/venv/Scripts/python.exe')
    if refresh_pyjwt:
        if installed_packages.get('pyjwt') != '2.10.1':
            raise ValueError('Installed PyJWT differs from the reviewed source version')
        installed_packages['pyjwt'] = '2.15.1'
    lines = []
    for name, number in sorted(installed_packages.items()):
        if not re.fullmatch(r'[a-z0-9][a-z0-9._-]*', name) or not re.fullmatch(r'[A-Za-z0-9.+!_-]+', number):
            raise ValueError('Unsupported installed package metadata')
        lines.append(name + '==' + number)
    lockfile = slot / 'installed-packages.txt'
    lockfile.write_text('\n'.join(lines) + '\n', encoding='utf-8')
    def locked_python(target_app, base_python, report):
        progress('Подготавливаем Python с проверенными версиями пакетов…')
        venv = target_app / 'runtime/venv'
        python = venv / 'Scripts/python.exe'
        if not python.exists():
            service.execute([base_python, '-m', 'venv', venv], report)
        if packages(python) != installed_packages:
            service.execute([python, '-m', 'pip', 'install', '--disable-pip-version-check',
                '--only-binary=:all:', '--no-deps', '-r', lockfile], report)
        (venv / 'requirements.sha256').write_text(sha(target_app / 'backend/requirements.txt'))
        return python
    old_setup, old_service = setup.ROOT, service.ROOT
    old_python = service.prepare_python
    try:
        setup.ROOT = service.ROOT = source
        service.prepare_python = locked_python
        progress('Готовим защищённый каталог программы и проверяем компоненты…')
        python, tools, _ = service.prepare_application(target, prepare_data, lambda _: None, cache=cache,
            service_id=spec.name, service_sid=spec.sid)
    finally:
        setup.ROOT, service.ROOT = old_setup, old_service
        service.prepare_python = old_python
        service.secure_directory(slot, admin_only=True)
        if prepare_data.exists(): service.secure_directory(prepare_data, admin_only=True)
    if installed_packages != packages(python):
        raise RuntimeError('Installed Python packages differ; maintenance update was refused before stopping the service')
    for prefix in ('backend', 'frontend/dist', 'infra'):
        expected = {name: digest for name, digest in candidate['files'].items() if name.startswith(prefix + '/')}
        if tree_manifest(target, prefix) != expected:
            raise RuntimeError('Prepared application files differ from the verified release')
    record = {'format': 1, 'service': spec.name, 'phase': 'prepared', 'archive_sha256': candidate['sha256'],
        'update_kind': 'pyjwt-security-refresh' if refresh_pyjwt else 'frontend-only' if frontend_only else 'maintenance',
        'old_app': str(app), 'new_app': str(target), 'old_version': old_version, 'new_version': candidate['version'],
        'old_xml': old_xml, 'created_at': time.time()}
    save_private(slot / 'update.json', record)
    return record


def write_xml(spec, text):
    pending = spec.xml.with_suffix('.xml.pending')
    with pending.open('w', encoding='utf-8') as stream:
        stream.write(text)
        stream.flush()
        os.fsync(stream.fileno())
    pending.replace(spec.xml)


def stop(spec):
    if query(spec)['State'] != 'Stopped':
        service.execute([spec.host, 'stopwait'], lambda _: None)
    if query(spec)['State'] != 'Stopped':
        raise RuntimeError('Service did not stop; code configuration was left for recovery')


def health(spec, timeout=150):
    profile = json.loads((spec.data / 'runtime-profile.json').read_text())
    origin_port = profile.get('origin_port', 5173)
    endpoint = f'http://127.0.0.1:{origin_port}/api/v1/health/live'
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            request = Request(endpoint, headers={'Host': profile['public_hostname'],
                'X-Forwarded-Proto': 'https', 'User-Agent': 'OwnDesk Update'})
            with urlopen(request, timeout=3) as response:
                if response.status != 200 or response.geturl() != endpoint or json.loads(response.read(4096)).get('status') != 'alive':
                    raise OSError('Local API is not ready')
            if query(spec)['State'] != 'Running':
                raise OSError('Service is not running')
            if spec.name in {'OwnDeskPortal', 'OwnDeskServer'}:
                if spec.name == 'OwnDeskServer':
                    status = json.loads((spec.data / 'portal-connection.json.status.json').read_text())
                    if status.get('state') != 'active' or not 0 <= time.time() - status.get('checked_at', 0) < 90:
                        raise OSError('Public connector is not ready')
                public_url = 'https://' + profile['public_hostname'] + '/api/v1/health/live'
                public = Request(public_url, headers={'User-Agent': 'OwnDesk/Windows Installer', 'Accept': 'application/json'})
                with urlopen(public, timeout=5) as response:
                    if response.status != 200 or response.geturl() != public_url or json.loads(response.read(4096)).get('status') != 'alive':
                        raise OSError('Public API is not ready')
            return
        except (OSError, ValueError): pass
        time.sleep(1)
    raise RuntimeError('Updated service did not become ready')


def journal(slot, record, phase):
    record.update(phase=phase, changed_at=time.time())
    save_private(slot / 'update.json', record)
    messages = {'prepared': 'Новая программа подготовлена.', 'backing-up': 'Создаём и проверяем свежую резервную копию…',
        'stopping': 'Подготовка завершена. Корректно останавливаем выбранную службу…',
        'switching': 'Переключаем программу, сохраняя рабочую базу…', 'starting': 'Запускаем службу и проверяем готовность…',
        'complete': 'Обновление завершено.', 'rolling-back': 'Возвращаем прежнюю программу с текущей базой…',
        'rolled-back': 'Прежняя программа запущена.', 'recovery-required': 'Для завершения нужен возврат запуска по журналу.'}
    if phase in messages: progress(messages[phase])


def rollback(spec, slot, record, check=None):
    """Both builds have the same backend and schema; retain all accepted writes."""
    if spec.xml.read_text(encoding='utf-8') not in {record['old_xml'], record.get('new_xml')}:
        raise RuntimeError('Service XML changed outside this transaction; manual recovery is required')
    journal(slot, record, 'rolling-back')
    stop(spec)
    write_xml(spec, record['old_xml'])
    service.execute([spec.host, 'start'], lambda _: None)
    (check or health)(spec)
    journal(slot, record, 'rolled-back')


def apply(spec, slot, record, backup_directory, check=None):
    if record.get('phase') != 'prepared' or record.get('service') != spec.name:
        raise ValueError('A fresh prepared update is required')
    app, current_xml = installed(spec)
    if str(app) != record['old_app'] or current_xml != record['old_xml'] or query(spec)['State'] != 'Running':
        raise RuntimeError('Service configuration changed since preparation')
    from native_backup import backup
    service.secure_directory(backup_directory, admin_only=True)
    journal(slot, record, 'backing-up')
    backup(spec.data, backup_directory, record['old_version'])
    record['backup_directory'] = str(backup_directory)
    root = ET.fromstring(record['old_xml'])
    target = Path(record['new_app'])
    root.find('workingdirectory').text = str(target)
    root.find('executable').text = str(target / 'runtime/venv/Scripts/python.exe')
    entrypoint = 'portal_runtime.py' if spec.account == 'NetworkService' else 'native_runtime.py'
    for key, action in (('startarguments', 'run'), ('stoparguments', 'stop')):
        root.find(key).text = subprocess.list2cmdline([str(target / 'infra' / entrypoint), action, '--data', str(spec.data)])
    record['new_xml'] = ET.tostring(root, encoding='unicode')
    try:
        journal(slot, record, 'stopping')
        stop(spec)
        journal(slot, record, 'switching')
        write_xml(spec, record['new_xml'])
        journal(slot, record, 'starting')
        service.execute([spec.host, 'start'], lambda _: None)
        (check or health)(spec)
        journal(slot, record, 'complete')
    except Exception:
        try:
            # Never restore the backup here: writes accepted by either build stay in the live DB.
            rollback(spec, slot, record)
        except Exception:
            journal(slot, record, 'recovery-required')
            raise RuntimeError('Rollback could not be verified; use recover --service ' + spec.name + ' --transaction ' + slot.name) from None
        raise RuntimeError('New code failed readiness; previous code restarted with the current database; transaction ' + slot.name) from None
    return record


def main():
    global _PROGRESS
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=('check', 'apply', 'recover'))
    parser.add_argument('--service', choices=('OwnDeskPortal', 'OwnDeskServer'), required=True)
    parser.add_argument('--archive', type=Path)
    parser.add_argument('--sha256')
    parser.add_argument('--transaction')
    parser.add_argument('--progress', action='store_true', help='Emit structured progress for the setup window')
    parser.add_argument('--frontend-only', action='store_true',
        help='Explicitly allow a reviewed portal frontend change; backend, schema, dependencies and runtime must match')
    parser.add_argument('--refresh-pyjwt', action='store_true',
        help='Reviewed portal security refresh: only PyJWT 2.10.1 to 2.15.1; application and runtime must match')
    args = parser.parse_args()
    if args.frontend_only and (args.service != 'OwnDeskPortal' or args.action == 'recover'):
        parser.error('--frontend-only is available only for portal check/apply; recover uses the recorded transaction')
    if args.refresh_pyjwt and (args.frontend_only or args.service != 'OwnDeskPortal' or args.action == 'recover'):
        parser.error('--refresh-pyjwt is available only for portal check/apply without --frontend-only')
    _PROGRESS = args.progress
    service.admin_required()
    name = 'Portal' if args.service == 'OwnDeskPortal' else 'Server'
    spec = Spec(args.service, Path(os.environ['ProgramFiles']) / 'OwnDesk' / name / 'OwnDeskService.exe',
        Path(os.environ['ProgramData']) / 'OwnDesk' / name, 'NetworkService' if name == 'Portal' else 'LocalService')
    base = Path(os.environ['ProgramData']) / 'OwnDesk/ServiceUpdates'
    service.secure_directory(base, admin_only=True)
    lockdir = base / args.service
    service.secure_directory(lockdir, admin_only=True)
    from local_snapshot import local_runtime_lock
    with local_runtime_lock(lockdir / 'updater'):
        pending = []
        for item in lockdir.glob('*/update.json'):
            no_links(item)
            previous = json.loads(item.read_text())
            if previous.get('phase') in {'stopping', 'switching', 'starting', 'rolling-back', 'recovery-required'}:
                pending.append(item.parent.name)
        if args.action != 'recover' and pending:
            raise RuntimeError('Recover unfinished transaction first: ' + ', '.join(pending))
        if args.action == 'recover':
            if not args.transaction or not re.fullmatch(r'[a-f0-9]{32}', args.transaction):
                parser.error('recover requires the transaction ID')
            slot = lockdir / args.transaction
            record = json.loads((slot / 'update.json').read_text())
            if record.get('service') != spec.name or record.get('phase') not in {'stopping', 'switching', 'starting', 'rolling-back', 'recovery-required'}:
                raise RuntimeError('This transaction does not require automatic code recovery')
            rollback(spec, slot, record)
        else:
            if not args.archive or not args.sha256:
                parser.error('--archive and trusted --sha256 are required')
            progress('Проверяем SHA-256 и структуру архива…')
            candidate = archive_manifest(args.archive, args.sha256)
            app, _ = installed(spec)
            current = compatibility(app, candidate, frontend_only=args.frontend_only, refresh_pyjwt=args.refresh_pyjwt)
            query(spec)
            progress('Проверка совместимости завершена.')
            if args.action == 'check':
                print(json.dumps({'status': 'compatible-pyjwt-security-refresh' if args.refresh_pyjwt else 'compatible-frontend-update' if args.frontend_only else 'compatible-maintenance-update',
                    'installed': current, 'candidate': candidate['version']}))
                return
            from uuid import uuid4
            slot = lockdir / uuid4().hex
            record = prepare(spec, args.archive, candidate, slot, frontend_only=args.frontend_only, refresh_pyjwt=args.refresh_pyjwt)
            backups = Path(os.environ['ProgramData']) / 'OwnDesk/Backups'
            service.secure_directory(backups, admin_only=True)
            apply(spec, slot, record, backups / (spec.name + '-before-' + slot.name))
        actual_version = record['old_version'] if record['phase'] == 'rolled-back' else record['new_version']
        print(json.dumps({'status': record['phase'], 'service': spec.name, 'version': actual_version, 'transaction': slot.name}))


if __name__ == '__main__':
    try:
        main()
    except (ValueError, RuntimeError, OSError, KeyError, subprocess.CalledProcessError) as error:
        print('OwnDesk: ' + str(error), file=sys.stderr)
        raise SystemExit(1)
