"""Maintenance-only image updates. Rollback switches code, never restores live data."""
import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import secrets
import shutil
import subprocess
import sys
import time
from urllib.request import Request, build_opener
from zipfile import ZipFile

from linux_backup import Installation, atomic, no_links, private_directory, safe_file, wait_database
from linux_backup_schedule import quote
from linux_setup import operation_lock, NoRedirect

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ('linux_update.py', 'linux_backup.py', 'linux_backup_worker.py', 'linux_backup_schedule.py', 'linux_setup.py')
SYSTEM = Path('/etc/systemd/system')
ID = re.compile(r'[a-f0-9]{24}')
SHA = re.compile(r'[a-f0-9]{64}')
CRITICAL_ROOT = ('compose.native.yaml', '.dockerignore')


def sha(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def version(value):
    if not re.fullmatch(r'[0-9]+\.[0-9]+\.[0-9]+', value):
        raise ValueError('Invalid release version')
    return tuple(map(int, value.split('.')))


def archive_manifest(path, expected):
    path = no_links(path)
    if not SHA.fullmatch(expected) or path.stat().st_nlink != 1 or sha(path) != expected:
        raise ValueError('Archive SHA-256 differs from the trusted metadata')
    files, seen = {}, set()
    with ZipFile(path) as package:
        entries = package.infolist()
        if len(entries) > 5000 or sum(item.file_size for item in entries) > 128 * 1024**2:
            raise ValueError('Release archive exceeds limits')
        for item in entries:
            raw = item.orig_filename.rstrip('/') if item.is_dir() else item.orig_filename
            name = PurePosixPath(raw)
            if (not raw or '\\' in raw or ':' in raw or any(ord(c) < 32 for c in raw)
                    or name.is_absolute() or name.as_posix() != raw or '..' in name.parts
                    or name.parts[0] != 'OwnDesk' or raw.casefold() in seen
                    or (item.external_attr >> 16) & 0o170000 not in (0, 0o100000, 0o040000)):
                raise ValueError('Unsafe or duplicate release path')
            seen.add(raw.casefold())
            if item.is_dir():
                continue
            if len(name.parts) < 2:
                raise ValueError('Invalid release root')
            relative = PurePosixPath(*name.parts[1:]).as_posix()
            if (set(name.parts) & {'.runtime', '.git', '__pycache__', 'node_modules'}
                    or (name.name.startswith('.env') and name.name != '.env.example')
                    or name.suffix.lower() in ('.pem', '.key', '.sqlite3', '.pyc', '.log')):
                raise ValueError('Release contains private or generated data')
            with package.open(item) as stream:
                files[relative] = hashlib.file_digest(stream, 'sha256').hexdigest()
        required = {'VERSION', 'backend/manage.py', 'backend/requirements.txt', 'frontend/dist/index.html',
            'infra/native_runtime.py', 'infra/native.Dockerfile', 'infra/linux_setup.py',
            'infra/linux_backup.py', 'infra/linux_backup_worker.py', 'infra/linux_backup_schedule.py',
            'infra/linux_update.py', 'OwnDesk-Update.sh', *CRITICAL_ROOT}
        if not required <= files.keys():
            raise ValueError('Incomplete release')
        lower = {name.casefold() for name in files}
        if any(parent.as_posix().casefold() in lower for name in files
               for parent in PurePosixPath(name).parents if str(parent) != '.'):
            raise ValueError('Release file/directory collision')
        release = package.read('OwnDesk/VERSION').decode().strip()
    version(release)
    return {'version': release, 'sha256': expected, 'files': files}


def critical(name):
    return (name in CRITICAL_ROOT or name.startswith(('backend/', 'frontend/dist/', 'infra/'))
            and name != 'infra/linux_update.py')


def installed_manifest(root):
    result = {}
    for prefix in ('backend', 'frontend/dist', 'infra'):
        for path in (root / prefix).rglob('*'):
            if path.is_symlink():
                raise ValueError('Installed code must not contain links')
            relative = path.relative_to(root).as_posix()
            if (path.is_file() and critical(relative) and path.suffix != '.pyc'
                    and not set(path.parts) & {'__pycache__', '.pytest_cache', '.ruff_cache'}):
                if path.stat().st_nlink != 1:
                    raise ValueError('Installed code must not contain hard links')
                result[relative] = sha(path)
    for name in CRITICAL_ROOT:
        path = no_links(root / name)
        result[name] = sha(path)
    return result


def compatibility(root, candidate):
    current = (root / 'VERSION').read_text().strip()
    if version(candidate['version']) <= version(current):
        raise ValueError('The release must be newer than the installed version')
    expected = {name: value for name, value in candidate['files'].items() if critical(name)}
    if installed_manifest(root) != expected:
        raise ValueError('Maintenance update refused: application, runtime, dependencies or Compose changed')
    return current


def text_atomic(path, value):
    temporary = path.with_name(path.name + '.' + secrets.token_hex(8) + '.tmp')
    try:
        with os.fdopen(os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), 'wb') as stream:
            stream.write(value)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(path)
        fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    finally:
        temporary.unlink(missing_ok=True)


IMAGE_PROBE = r'''import hashlib,importlib.metadata,json,os
from pathlib import Path
files={}
for prefix in ('backend','frontend/dist','infra'):
 for p in (Path('/app')/prefix).rglob('*'):
  name=p.relative_to('/app').as_posix()
  if p.is_file() and p.suffix!='.pyc' and '__pycache__' not in p.parts and name!='infra/linux_update.py':
   files[name]=hashlib.sha256(p.read_bytes()).hexdigest()
print(json.dumps({'files':files,'version':Path('/app/VERSION').read_text().strip(),'uid':os.getuid(),
 'caddy_capability':'security.capability' in os.listxattr('/usr/local/bin/caddy'),
 'packages':sorted((d.metadata['Name'].lower(),d.version) for d in importlib.metadata.distributions())}))
'''


class Updater:
    def __init__(self, root, timeout=180):
        self.installation = Installation(root)
        self.root, self.state = self.installation.root, self.installation.state
        self.docker = self.installation.docker
        self.pending = self.state / 'update-pending.json'
        self.last = self.state / 'update-last.json'
        self.timeout = max(30, min(600, timeout))

    def path(self, identity):
        if not ID.fullmatch(str(identity)):
            raise ValueError('Invalid transition identity')
        return private_directory(self.state / 'updates' / identity)

    def read(self, path):
        pointer = json.loads(safe_file(self.state, path.name).read_text())
        folder = self.path(pointer['id'])
        record = json.loads(safe_file(folder, 'transition.json').read_text())
        if record.get('format') != 1 or record.get('project') != self.installation.project or record.get('id') != pointer['id']:
            raise ValueError('Invalid transition journal')
        return record

    def save(self, record):
        atomic(self.path(record['id']) / 'transition.json', record)

    def guard(self):
        if self.pending.exists() or self.installation.marker.exists():
            raise RuntimeError('Pending maintenance exists; run recover or backup resume first')

    def probe(self, image):
        result = self.docker.json(['run', '--rm', '--network', 'none', '--read-only', '--cap-drop', 'ALL',
            '--security-opt', 'no-new-privileges:true', '--entrypoint', 'python', image, '-c', IMAGE_PROBE])
        if result['uid'] != 10001 or result['caddy_capability']:
            raise ValueError('Unexpected candidate image privileges')
        return result

    def check(self, archive, expected):
        self.guard()
        candidate = archive_manifest(archive, expected)
        current = compatibility(self.root, candidate)
        app = self.installation.container('app')
        if app['Config']['Image'] != 'owndesk-native:' + current:
            raise ValueError('Installed image/version differs; recover the previous operation first')
        return candidate, current

    def volumes(self, app):
        items = [item['Name'] for item in app['Mounts'] if item['Destination'] == '/data' and item['Type'] == 'volume']
        if len(items) != 1:
            raise ValueError('Expected one organization volume')
        return items[0]

    def validate(self, record, target):
        deadline = time.monotonic() + self.timeout
        expected_image, expected_version = record[target + '_image'], record[target + '_version']
        while time.monotonic() < deadline:
            try:
                app = self.installation.container('app')
                if app['Image'] != expected_image or self.volumes(app) != record['data_volume']:
                    raise ValueError('Unexpected image or data volume after switching')
                if app['State'].get('Health', {}).get('Status') != 'healthy':
                    raise RuntimeError('Application is not healthy yet')
                actual = self.docker.run(['exec', app['Id'], 'cat', '/app/VERSION']).decode().strip()
                if actual != expected_version:
                    raise ValueError('Running application version differs')
                origin = record['origin']
                request = Request(origin + '/api/v1/health/live', headers={
                    'User-Agent': 'OwnDesk/' + expected_version + ' Installer', 'Accept': 'application/json'})
                with build_opener(NoRedirect()).open(request, timeout=5) as response:
                    body = response.read(4097)
                    if (response.status != 200 or response.geturl() != origin + '/api/v1/health/live'
                            or len(body) > 4096 or json.loads(body).get('status') != 'alive'):
                        raise RuntimeError('Public API is not healthy')
                return
            except (OSError, RuntimeError, ValueError, KeyError):
                time.sleep(2)
        raise RuntimeError('Updated application/public readiness timed out')

    def hook(self, folder):
        # Keep the recovery driver outside host files that later tool releases may replace.
        driver = folder / 'driver'
        driver.mkdir(mode=0o700)
        for name in TOOLS:
            source = no_links(Path(__file__).with_name(name))
            text_atomic(driver / name, source.read_bytes())
        subprocess.run([sys.executable, str(driver / 'linux_update.py'), '--help'], check=True, capture_output=True)
        name = 'owndesk-update-recovery-' + self.installation.project.removeprefix('owndesk-') + '.service'
        path = SYSTEM / name
        header = '# OwnDesk update recovery ' + self.installation.project + '\n'
        if path.is_symlink() or path.exists() and not path.read_text().startswith(header):
            raise ValueError('Unrelated update recovery unit exists')
        text = (header + '[Unit]\nDescription=OwnDesk interrupted update recovery\nRequires=docker.service\nAfter=docker.service\nBefore=owndesk-backup-'
            + self.installation.project.removeprefix('owndesk-') + '-resume.service\n\n[Service]\nType=oneshot\nUMask=0077\nTimeoutStartSec=15min\nExecStart=:/usr/bin/python3 '
            + quote(driver / 'linux_update.py') + ' recover --root ' + quote(self.root)
            + '\n\n[Install]\nWantedBy=multi-user.target\n')
        text_atomic(path, text.encode())
        subprocess.run(['systemd-analyze', 'verify', str(path)], check=True, capture_output=True)
        subprocess.run(['systemctl', 'daemon-reload'], check=True, capture_output=True)
        subprocess.run(['systemctl', 'enable', name], check=True, capture_output=True)

    def begin(self, record):
        self.save(record)
        if self.installation.marker.exists():
            marker = json.loads(safe_file(self.state, self.installation.marker.name).read_text())
            if marker.get('kind') != 'linux-update' or marker.get('id') != record['id']:
                raise ValueError('A different maintenance journal exists')
        atomic(self.installation.marker, {'project': self.installation.project, 'kind': 'linux-update', 'id': record['id']})
        atomic(self.pending, {'id': record['id']})

    def finish(self, record, state):
        record['state'] = state
        record['transition_until'] = 0
        self.save(record)
        atomic(self.last, {'id': record['id']})
        # A power cut between these deletes is handled by recover reading the committed journal.
        self.installation.marker.unlink(missing_ok=True)
        self.pending.unlink(missing_ok=True)

    def switch(self, record, target):
        app = self.installation.container('app')
        db = self.installation.container('db')
        if (db['Id'] != record['db_id'] or app['Image'] not in (record['old_image'], record['new_image'])
                or self.volumes(app) != record['data_volume']):
            raise ValueError('Installed containers changed outside this transition')
        remaining = min(65, max(0, record.get('transition_until', 0) - time.time()))
        if remaining:
            time.sleep(remaining)
        image, release = record[target + '_image'], record[target + '_version']
        self.docker.run(['image', 'inspect', image])
        if not db['State']['Running']:
            self.docker.run(['start', db['Id']])
        wait_database(self.docker, db['Id'], 'owndesk_admin')
        if app['State']['Running']:
            record['transition_until'] = time.time() + 65
            self.save(record)
            self.docker.run(['stop', '-t', '60', app['Id']])
            record['transition_until'] = 0
            self.save(record)
        self.docker.run(['tag', image, 'owndesk-native:' + release])
        text_atomic(self.root / 'VERSION', (release + '\n').encode())
        if target == 'old':
            env = safe_file(self.path(record['id']), 'previous.env').read_bytes()
        else:
            settings = self.installation.settings
            env = ('POSTGRES_PASSWORD=' + settings['db_admin_password'] + '\nPOSTGRES_APP_PASSWORD='
                + settings['db_app_password'] + '\nOWNDESK_VERSION=' + release + '\n').encode()
        text_atomic(self.state / 'compose.env', env)
        self.docker.run([*self.installation.base, 'up', '-d', '--no-deps', '--no-build', '--pull', 'never', '--force-recreate', 'app'])

    def apply(self, archive, expected, backup_store):
        candidate, current = self.check(archive, expected)
        app, db = self.installation.container('app'), self.installation.container('db')
        if not app['State']['Running'] or not db['State']['Running'] or app['State'].get('Health', {}).get('Status') != 'healthy':
            raise RuntimeError('Start and verify the existing installation before updating')
        identity = secrets.token_hex(12)
        folder = private_directory(self.state / 'updates', create=True) / identity
        folder.mkdir(mode=0o700)
        saved = folder / 'release.zip'
        shutil.copyfile(archive, saved)
        saved.chmod(0o600)
        if archive_manifest(saved, expected) != candidate:
            raise ValueError('Release changed during staging')
        release = folder / 'release'
        release.mkdir(mode=0o700)
        with ZipFile(saved) as package:
            for name in candidate['files']:
                path = release / name
                path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                with package.open('OwnDesk/' + name) as source:
                    with path.open('xb') as target:
                        shutil.copyfileobj(source, target)
                path.chmod(0o600)
        self.docker.run(['build', '-f', str(release / 'infra/native.Dockerfile'), '-t',
            'owndesk-native:' + candidate['version'], str(release)], timeout=3600)
        image = self.docker.json(['image', 'inspect', 'owndesk-native:' + candidate['version']])[0]['Id']
        proposed, previous = self.probe(image), self.probe(app['Image'])
        expected_files = {name: value for name, value in candidate['files'].items() if critical(name) and name not in CRITICAL_ROOT}
        if (proposed['files'] != expected_files or proposed['version'] != candidate['version']
                or previous['files'] != expected_files or proposed['packages'] != previous['packages']):
            raise ValueError('Built image code or installed Python dependencies differ')
        # Read the source profile without exposing its connector credentials.
        profile = self.docker.json(['exec', app['Id'], 'python', '-c',
            "import json; d=json.load(open('/data/runtime-profile.json')); print(json.dumps({'hostname':d['public_hostname']}))"])
        if not re.fullmatch(r'org-[a-f0-9]{32}\.owndesk\.work', profile['hostname']):
            raise ValueError('Unexpected organization hostname')
        copied = self.installation.backup(backup_store)
        text_atomic(folder / 'previous.env', safe_file(self.state, 'compose.env').read_bytes())
        record = {'format': 1, 'id': identity, 'project': self.installation.project, 'state': 'prepared',
            'old_image': app['Image'], 'old_version': current, 'new_image': image, 'new_version': candidate['version'],
            'db_id': db['Id'], 'data_volume': self.volumes(app), 'origin': 'https://' + profile['hostname'],
            'archive_sha256': expected, 'backup': copied['path'], 'transition_until': 0}
        self.hook(folder)
        self.begin(record)
        try:
            self.switch(record, 'new')
            self.validate(record, 'new')
            # Install only the updater entrypoints. All existing runtime/host tools were pinned by compatibility().
            text_atomic(self.root / 'infra/linux_update.py', (release / 'infra/linux_update.py').read_bytes())
            text_atomic(self.root / 'OwnDesk-Update.sh', (release / 'OwnDesk-Update.sh').read_bytes())
            self.finish(record, 'committed')
            return {'status': 'updated', 'version': candidate['version'], 'transition': identity, 'backup': copied['path']}
        except BaseException:
            self.switch(record, 'old')
            self.validate(record, 'old')
            self.finish(record, 'failed-rolled-back')
            raise RuntimeError('Update failed; previous image restored, current database retained') from None

    def recover(self):
        if not self.pending.exists():
            if self.installation.marker.exists():
                marker = json.loads(safe_file(self.state, self.installation.marker.name).read_text())
                if marker.get('kind') != 'linux-update' or marker.get('project') != self.installation.project:
                    raise ValueError('Resume the interrupted backup before recovering an update')
                self.path(marker.get('id'))
                atomic(self.pending, {'id': marker['id']})
            elif self.last.exists():
                record = self.read(self.last)
                if record['state'] != 'rolling-back':
                    return {'status': 'no-pending-update'}
                self.begin(record)
            else:
                return {'status': 'no-pending-update'}
        record = self.read(self.pending)
        if record['state'] in ('committed', 'rolled-back', 'failed-rolled-back'):
            self.validate(record, 'new' if record['state'] == 'committed' else 'old')
            self.finish(record, record['state'])
        else:
            self.switch(record, 'old')
            self.validate(record, 'old')
            self.finish(record, 'failed-rolled-back')
        return {'status': 'recovered', 'database_restored': False}

    def rollback(self, backup_store):
        self.guard()
        record = self.read(self.last)
        app = self.installation.container('app')
        if record['state'] != 'committed' or app['Image'] != record['new_image']:
            raise ValueError('Only the latest committed update can be rolled back')
        copied = self.installation.backup(backup_store)
        record['rollback_backup'] = copied['path']
        record['state'] = 'rolling-back'
        self.begin(record)
        self.switch(record, 'old')
        self.validate(record, 'old')
        self.finish(record, 'rolled-back')
        return {'status': 'rolled-back', 'version': record['old_version'], 'database_restored': False}


def main():
    parser = argparse.ArgumentParser(description='OwnDesk Linux maintenance updates without database rewind')
    parser.add_argument('action', choices=('check', 'apply', 'rollback', 'recover', 'status'))
    parser.add_argument('--root', type=Path, default=ROOT)
    parser.add_argument('--archive', type=Path)
    parser.add_argument('--sha256')
    parser.add_argument('--backup-dir', type=Path, default=Path('/var/backups/owndesk'))
    parser.add_argument('--timeout', type=int, default=180)
    args = parser.parse_args()
    if sys.platform != 'linux' or os.geteuid() != 0:
        raise RuntimeError('Run with sudo on the Ubuntu server')
    os.umask(0o077)
    with operation_lock(args.root):
        updater = Updater(args.root, args.timeout)
        if args.action in ('check', 'apply'):
            if args.archive is None or not args.sha256:
                parser.error('--archive and --sha256 are required')
            if args.action == 'check':
                candidate, current = updater.check(args.archive, args.sha256)
                result = {'status': 'compatible-maintenance-update', 'installed': current, 'candidate': candidate['version']}
            else:
                result = updater.apply(args.archive, args.sha256, args.backup_dir)
        elif args.action == 'recover':
            result = updater.recover()
        elif args.action == 'rollback':
            result = updater.rollback(args.backup_dir)
        else:
            result = {'pending': updater.read(updater.pending) if updater.pending.exists() else None,
                      'last': updater.read(updater.last) if updater.last.exists() else None}
        print(json.dumps(result))


if __name__ == '__main__':
    try:
        main()
    except (RuntimeError, ValueError, OSError, KeyError, subprocess.SubprocessError) as error:
        print('OwnDesk: ' + str(error), file=sys.stderr)
        raise SystemExit(1)
