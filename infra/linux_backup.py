"""Cold backups and quarantined restore checks for native Linux Compose installs."""
import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import secrets
import shutil
import stat
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
PG_IMAGE = 'postgres:17.11-bookworm'
PROJECT = re.compile(r'owndesk-[0-9a-f]{12}')
VERSION = re.compile(r'[0-9]+\.[0-9]+\.[0-9]+')
IMAGE_ID = re.compile(r'sha256:[a-f0-9]{64}')
REQUIRED = {'database.dump', 'installer/installation.json', 'installer/compose.env',
            'data/secrets.json', 'data/runtime-profile.json', 'data/portal-connection.json'}


def no_links(path):
    path = path.absolute()
    if ',' in str(path) or any(ord(char) < 32 for char in str(path)):
        raise ValueError('Control characters and commas are not supported in Docker mount paths')
    for ancestor in [path, *path.parents]:
        if ancestor.is_symlink():
            raise ValueError('Paths must not contain symbolic links')
    return path.resolve(strict=False)


def private_directory(path, create=False):
    path = no_links(path)
    if os.name == 'posix':
        for ancestor in path.parents:
            if not ancestor.exists():
                continue
            parent = ancestor.stat()
            if parent.st_uid != 0 or (parent.st_mode & 0o022 and not parent.st_mode & stat.S_ISVTX):
                raise ValueError('Use root-owned installation and backup parent directories')
    if create:
        path.mkdir(parents=True, exist_ok=True, mode=0o700)
    info = path.stat()
    if not stat.S_ISDIR(info.st_mode):
        raise ValueError('Expected a directory')
    if os.name == 'posix' and (info.st_uid != 0 or info.st_mode & 0o077):
        raise ValueError('Choose a root-owned directory with mode 0700')
    return path


def safe_file(root, name):
    value = PurePosixPath(name)
    if (not name or value.is_absolute() or value.as_posix() != name or '..' in value.parts
            or '\\' in name or ':' in name):
        raise ValueError('Invalid backup file name')
    path = no_links(root / name)
    info = path.stat()
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        raise ValueError('Expected a single regular file')
    if os.name == 'posix' and (info.st_uid != 0 or info.st_mode & 0o077):
        raise ValueError('Backup files must be private to root')
    return path


def fingerprint(path):
    with path.open('rb') as stream:
        return {'size': path.stat().st_size, 'sha256': hashlib.file_digest(stream, 'sha256').hexdigest()}


def atomic(path, value):
    temporary = path.with_name(path.name + '.' + secrets.token_hex(8) + '.tmp')
    try:
        with os.fdopen(os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), 'w') as stream:
            json.dump(value, stream, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        if os.name == 'posix':
            fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
    finally:
        temporary.unlink(missing_ok=True)


def verify(directory):
    directory = private_directory(directory)
    manifest = json.loads(safe_file(directory, 'manifest.json').read_text())
    if (manifest.get('format') != 1 or manifest.get('kind') != 'linux-compose'
            or not VERSION.fullmatch(str(manifest.get('version', '')))
            or not PROJECT.fullmatch(str(manifest.get('project', '')))
            or not IMAGE_ID.fullmatch(str(manifest.get('app_image', '')))
            or not IMAGE_ID.fullmatch(str(manifest.get('pg_image', '')))):
        raise ValueError('Unsupported Linux backup manifest')
    files, counts = manifest.get('files'), manifest.get('counts')
    if (not isinstance(files, dict) or not REQUIRED <= files.keys()
            or not isinstance(counts, dict) or not counts
            or any(not isinstance(name, str) or not re.fullmatch(r'[a-z_][a-z0-9_]*', name) or type(count) is not int or count < 0
                   for name, count in counts.items())):
        raise ValueError('Incomplete Linux backup')
    for name, expected in files.items():
        if (not isinstance(expected, dict) or type(expected.get('size')) is not int or expected['size'] < 0
                or not re.fullmatch(r'[a-f0-9]{64}', str(expected.get('sha256', '')))):
            raise ValueError('Invalid file fingerprint')
        if fingerprint(safe_file(directory, name)) != expected:
            raise ValueError('Backup file checksum differs: ' + name)
    # Check every entry, including unlisted objects, before a container sees the directory.
    for path in directory.rglob('*'):
        if path.is_symlink() or (not path.is_dir() and not path.is_file()):
            raise ValueError('Unexpected object in backup directory')
        if path.is_file() and path.relative_to(directory).as_posix() not in {*files, 'manifest.json'}:
            raise ValueError('Unlisted file in backup directory')
        if path.is_dir():
            private_directory(path)
    settings = json.loads(safe_file(directory, 'installer/installation.json').read_text())
    validate_settings(settings)
    if settings['project'] != manifest['project']:
        raise ValueError('Backup installation identity differs')
    return manifest


def validate_settings(value):
    if not isinstance(value, dict) or not PROJECT.fullmatch(str(value.get('project', ''))):
        raise ValueError('Invalid Compose project name')
    for field in ('db_admin_password', 'db_app_password'):
        if not re.fullmatch(r'[A-Za-z0-9_-]{32,128}', str(value.get(field, ''))):
            raise ValueError('Invalid installation credentials')


class Docker:
    def __init__(self, log):
        self.log = log

    def run(self, arguments, *, data=None, stdout=subprocess.PIPE, timeout=600):
        with self.log.open('ab') as log:
            done = subprocess.run(['docker', *map(str, arguments)], input=data, stdout=stdout,
                                  stderr=log, timeout=timeout)
        if done.returncode:
            raise RuntimeError('Docker operation failed; see the private backup operation log')
        return done.stdout

    def json(self, arguments):
        return json.loads(self.run(arguments))

    def inspect(self, name):
        return self.json(['inspect', name])[0]


def table_counts(docker, container, user):
    command = ['exec', container, 'psql', '-U', user, '-d', 'owndesk', '-At', '-v', 'ON_ERROR_STOP=1', '-c']
    tables = docker.run([*command, "SELECT tablename FROM pg_tables WHERE schemaname='public' ORDER BY tablename"]).decode().splitlines()
    if not tables or any(not re.fullmatch(r'[a-z_][a-z0-9_]*', table) for table in tables):
        raise ValueError('Unexpected database table name')
    query = ' UNION ALL '.join("SELECT '" + table + "',count(*) FROM public.\"" + table + '"' for table in tables)
    return {name: int(count) for name, count in (line.split('|') for line in docker.run([*command, query]).decode().splitlines())}


def wait_database(docker, container, user):
    deadline = time.monotonic() + 120
    while time.monotonic() < deadline:
        try:
            docker.run(['exec', container, 'pg_isready', '-h', '127.0.0.1', '-U', user, '-d', 'owndesk'], timeout=10)
            return
        except RuntimeError:
            time.sleep(1)
    raise RuntimeError('PostgreSQL readiness timed out')


def helper(docker, image, mounts, action, *arguments):
    return docker.json(['run', '--rm', '--network', 'none', '--read-only', '--user', 'root',
        '--cap-drop', 'ALL', '--cap-add', 'DAC_OVERRIDE', '--cap-add', 'CHOWN', '--cap-add', 'FOWNER',
        '--security-opt', 'no-new-privileges:true',
        '--mount', 'type=bind,src=' + str(Path(__file__).with_name('linux_backup_worker.py')) + ',dst=/backup-worker.py,readonly',
        *mounts, '--entrypoint', 'python', image, '/backup-worker.py', action, *arguments])


class Installation:
    def __init__(self, root):
        self.root = no_links(root)
        self.state = private_directory(root / '.runtime/linux')
        self.settings = json.loads(safe_file(self.state, 'installation.json').read_text())
        validate_settings(self.settings)
        self.project = self.settings['project']
        safe_file(self.state, 'compose.env')
        self.log = self.state / 'backup-operations.log'
        if self.log.exists():
            safe_file(self.state, self.log.name)
        self.docker = Docker(self.log)
        self.base = ['compose', '--project-name', self.project, '--project-directory', str(self.root),
            '--env-file', str(self.state / 'compose.env'), '-f', str(self.root / 'compose.native.yaml')]
        self.marker = self.state / 'backup-recovery.json'

    def container(self, service):
        identity = self.docker.run([*self.base, 'ps', '-a', '-q', service]).decode().strip()
        if not re.fullmatch(r'[a-f0-9]{12,64}', identity):
            raise RuntimeError('Existing app and database containers are required')
        details = self.docker.inspect(identity)
        self.validate_container(details, service)
        return details

    def validate_container(self, value, service):
        labels = value['Config'].get('Labels', {})
        if (labels.get('com.docker.compose.project') != self.project
                or labels.get('com.docker.compose.service') != service
                or labels.get('com.docker.compose.project.working_dir') != str(self.root)):
            raise ValueError('Container does not belong to this installation')

    def resume(self):
        if not self.marker.exists():
            return {'resumed': False}
        record = json.loads(safe_file(self.state, self.marker.name).read_text())
        if record.get('project') != self.project or set(record.get('containers', {})) != {'db', 'app'}:
            raise ValueError('Unexpected recovery journal')
        for service in ('db', 'app'):
            value = record['containers'][service]
            if not re.fullmatch(r'[a-f0-9]{64}', str(value.get('id', ''))) or type(value.get('running')) is not bool:
                raise ValueError('Invalid recovery container identity')
            self.validate_container(self.docker.inspect(value['id']), service)
        # An interrupted docker stop can keep running in the daemon after its client died.
        remaining = min(65, max(0, record.get('transition_until', 0) - time.time()))
        if remaining:
            time.sleep(remaining)
        for service in ('db', 'app'):
            value = record['containers'][service]
            details = self.docker.inspect(value['id'])
            self.validate_container(details, service)
            if type(value.get('running')) is not bool:
                raise ValueError('Unexpected recovery running state')
            if value['running']:
                self.docker.run(['start', details['Id']])
                if service == 'db':
                    wait_database(self.docker, details['Id'], 'owndesk_admin')
        for service in ('app', 'db'):
            value = record['containers'][service]
            if not value['running'] and self.docker.inspect(value['id'])['State']['Running']:
                self.docker.run(['stop', '-t', '60', value['id']])
        self.marker.unlink()
        return {'resumed': True}

    def backup(self, store):
        self.resume()
        store = no_links(store)
        if store.is_relative_to(self.root) or self.root.is_relative_to(store):
            raise ValueError('Backup storage must be separate from the installation')
        store = private_directory(store, create=True)
        app, db = self.container('app'), self.container('db')
        tag = app['Config']['Image']
        match = re.fullmatch(r'owndesk-native:([0-9]+\.[0-9]+\.[0-9]+)', tag)
        if not match or db['Config']['Image'] != PG_IMAGE:
            raise ValueError('Unexpected installed image profile')
        from urllib.parse import urlsplit, unquote
        app_env = dict(item.split('=', 1) for item in app['Config'].get('Env', []) if '=' in item)
        db_env = dict(item.split('=', 1) for item in db['Config'].get('Env', []) if '=' in item)
        database = urlsplit(app_env.get('DATABASE_URL', ''))
        if (database.scheme != 'postgresql' or database.hostname != 'db' or database.username != 'owndesk'
                or database.path != '/owndesk' or unquote(database.password or '') != self.settings['db_app_password']
                or db_env.get('POSTGRES_PASSWORD') != self.settings['db_admin_password']):
            raise ValueError('Installed database credentials differ from installation metadata')
        volumes = [item['Name'] for item in app['Mounts'] if item['Type'] == 'volume' and item['Destination'] == '/data']
        if len(volumes) != 1:
            raise ValueError('Expected one organization data volume')
        volume = self.docker.json(['volume', 'inspect', volumes[0]])[0]
        if volume.get('Labels', {}).get('com.docker.compose.project') != self.project:
            raise ValueError('Organization volume has an unexpected project label')
        destination = store / ('backup-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ-') + secrets.token_hex(6))
        destination.mkdir(mode=0o700)
        record = {'project': self.project, 'transition_until': 0, 'containers': {
            'app': {'id': app['Id'], 'running': app['State']['Running']},
            'db': {'id': db['Id'], 'running': db['State']['Running']}}}
        if record['containers']['app']['running'] and not record['containers']['db']['running']:
            raise RuntimeError('Application is running without its database')
        atomic(self.marker, record)
        try:
            if app['State']['Running']:
                record['transition_until'] = time.time() + 65
                atomic(self.marker, record)
                self.docker.run(['stop', '-t', '60', app['Id']])
                record['transition_until'] = 0
                atomic(self.marker, record)
            if not db['State']['Running']:
                self.docker.run(['start', db['Id']])
            wait_database(self.docker, db['Id'], 'owndesk_admin')
            counts = table_counts(self.docker, db['Id'], 'owndesk_admin')
            (destination / 'installer').mkdir(mode=0o700)
            files = {}
            for name in ('installation.json', 'compose.env'):
                source = safe_file(self.state, name)
                target = destination / 'installer' / name
                shutil.copyfile(source, target)
                target.chmod(0o600)
                files['installer/' + name] = fingerprint(target)
            with os.fdopen(os.open(destination / 'database.dump', os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), 'wb') as stream:
                self.docker.run(['exec', db['Id'], 'pg_dump', '-U', 'owndesk_admin', '-d', 'owndesk',
                    '-Fc', '--no-owner', '--no-privileges'], stdout=stream, timeout=3600)
            files['database.dump'] = fingerprint(destination / 'database.dump')
            files.update(helper(self.docker, app['Image'], [
                '--mount', 'type=volume,src=' + volumes[0] + ',dst=/source,readonly',
                '--mount', 'type=bind,src=' + str(destination) + ',dst=/backup'], 'export'))
            for name in ('installation.json', 'compose.env'):
                if fingerprint(safe_file(self.state, name)) != files['installer/' + name]:
                    raise RuntimeError('Installation configuration changed during backup')
            atomic(destination / 'manifest.json', {'format': 1, 'kind': 'linux-compose', 'version': match[1],
                'project': self.project, 'app_image': app['Image'], 'pg_image': db['Image'],
                'completed_at': time.time(), 'counts': counts, 'files': files})
            manifest = verify(destination)
            return {'status': 'verified', 'path': str(destination), 'version': manifest['version'],
                    'tables': len(counts), 'files': len(files)}
        finally:
            self.resume()


def restore_check(directory):
    manifest = verify(directory)
    log = directory.parent / ('restore-' + secrets.token_hex(8) + '.log')
    log.touch(mode=0o600, exist_ok=False)
    docker = Docker(log)
    app = docker.json(['image', 'inspect', manifest['app_image']])[0]
    pg = docker.json(['image', 'inspect', PG_IMAGE])[0]
    if ('owndesk-native:' + manifest['version'] not in (app.get('RepoTags') or [])
            or pg['Id'] != manifest['pg_image']):
        raise ValueError('The original verified application and PostgreSQL images must be present locally')
    name = 'owndesk-restore-' + secrets.token_hex(12)
    network, pgvolume, datavolume, container = (name + suffix for suffix in ('-net', '-pg', '-data', '-db'))
    created = []
    private = directory.parent / (name + '-private')
    private.mkdir(mode=0o700)
    result = None
    cleanup_errors = []
    try:
        docker.run(['network', 'create', '--internal', '--label', 'owndesk.restore-check=' + name, network])
        created.append(('network', network))
        for volume in (pgvolume, datavolume):
            docker.run(['volume', 'create', '--label', 'owndesk.restore-check=' + name, volume])
            created.append(('volume', volume))
        uid = docker.run(['run', '--rm', '--network', 'none', '--entrypoint', 'id', pg['Id'], '-u', 'postgres']).decode().strip()
        gid = docker.run(['run', '--rm', '--network', 'none', '--entrypoint', 'id', pg['Id'], '-g', 'postgres']).decode().strip()
        if not uid.isdigit() or not gid.isdigit() or not 0 < int(uid) < 65536 or not 0 < int(gid) < 65536:
            raise ValueError('Invalid PostgreSQL container identity')
        helper(docker, app['Id'], ['--mount', 'type=volume,src=' + pgvolume + ',dst=/pgdata'], 'prepare-pg', '--uid', uid, '--gid', gid)
        envfile = private / 'postgres.env'
        envfile.write_text('POSTGRES_USER=restore_admin\nPOSTGRES_DB=owndesk\nPOSTGRES_PASSWORD=' + secrets.token_urlsafe(36) + '\n')
        docker.run(['run', '-d', '--name', container, '--network', network, '--user', uid + ':' + gid,
            '--read-only', '--cap-drop', 'ALL', '--security-opt', 'no-new-privileges:true',
            '--tmpfs', '/tmp:rw,nosuid,noexec,size=64m',
            '--tmpfs', '/var/run/postgresql:rw,nosuid,noexec,uid=' + uid + ',gid=' + gid,
            '--env-file', str(envfile), '--mount', 'type=volume,src=' + pgvolume + ',dst=/var/lib/postgresql/data', pg['Id']])
        created.append(('container', container))
        isolated = docker.inspect(container)
        isolated_network = docker.json(['network', 'inspect', network])[0]
        host = isolated['HostConfig']
        if (not isolated_network.get('Internal') or host.get('PortBindings')
                or not host.get('ReadonlyRootfs') or host.get('CapDrop') != ['ALL']
                or 'no-new-privileges:true' not in host.get('SecurityOpt', [])
                or isolated['Config']['User'] != uid + ':' + gid
                or any(item['Type'] == 'bind' for item in isolated['Mounts'])):
            raise RuntimeError('Restore database isolation was not established')
        wait_database(docker, container, 'restore_admin')
        password = json.loads(safe_file(directory, 'installer/installation.json').read_text())['db_app_password']
        sql = ("CREATE ROLE owndesk LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE PASSWORD '" + password
               + "'; ALTER DATABASE owndesk OWNER TO owndesk;")
        docker.run(['exec', '-i', container, 'psql', '-U', 'restore_admin', '-d', 'owndesk', '-v', 'ON_ERROR_STOP=1'], data=sql.encode())
        # Stream the dump without loading it into memory or mounting the backup into PostgreSQL.
        with safe_file(directory, 'database.dump').open('rb') as stream, log.open('ab') as errors:
            done = subprocess.run(['docker', 'exec', '-i', container, 'pg_restore', '--exit-on-error', '--no-owner',
                '--no-privileges', '--role', 'owndesk', '-U', 'restore_admin', '-d', 'owndesk'],
                stdin=stream, stdout=errors, stderr=errors, timeout=3600)
            if done.returncode:
                raise RuntimeError('Isolated restore failed; inspect the private restore log')
        if table_counts(docker, container, 'owndesk') != manifest['counts']:
            raise RuntimeError('Restored table counts differ')
        flags = docker.run(['exec', container, 'psql', '-U', 'restore_admin', '-d', 'owndesk', '-At', '-c',
            "SELECT rolsuper,rolcreatedb,rolcreaterole FROM pg_roles WHERE rolname='owndesk'"]).decode().strip()
        if flags != 'f|f|f':
            raise RuntimeError('Application role gained server privileges')
        files = helper(docker, app['Id'], ['--mount', 'type=bind,src=' + str(directory) + ',dst=/backup,readonly',
            '--mount', 'type=volume,src=' + datavolume + ',dst=/restored'], 'restore')
        result = {'status': 'restore-verified', 'version': manifest['version'], 'tables': len(manifest['counts']),
                  'restricted_role_verified': True, 'application_started': False, 'live_data_replaced': False,
                  'network_internal': True, 'database_nonroot': True, 'database_read_only': True,
                  'no_published_ports': True, 'backup_not_mounted_in_database': True, **files}
    finally:
        for kind, value in reversed(created):
            try:
                if kind == 'container':
                    docker.run(['stop', '-t', '30', value])
                    docker.run(['rm', value])
                else:
                    docker.run([kind, 'rm', value])
            except Exception:
                cleanup_errors.append(value)
        # Remove only the generated, flat credential directory, never a backup or data volume tree.
        (private / 'postgres.env').unlink(missing_ok=True)
        private.rmdir()
        if cleanup_errors:
            raise RuntimeError('Restore check cleanup incomplete; inspect private log: ' + str(log))
    result['temporary_resources_removed'] = True
    return result


def main():
    parser = argparse.ArgumentParser(description='OwnDesk Linux cold backup and isolated restore verification')
    parser.add_argument('action', choices=('backup', 'verify', 'restore-check', 'resume', 'schedule', 'unschedule'))
    parser.add_argument('--root', type=Path, default=ROOT)
    parser.add_argument('--output-dir', type=Path, default=Path('/var/backups/owndesk'))
    parser.add_argument('--input', type=Path)
    parser.add_argument('--at', default='02:30')
    args = parser.parse_args()
    if sys.platform != 'linux' or os.geteuid() != 0:
        raise RuntimeError('Run this command with sudo on the Ubuntu server')
    os.umask(0o077)
    if args.action in ('verify', 'restore-check'):
        if args.input is None:
            parser.error('--input is required')
        directory = private_directory(args.input)
        if args.action == 'verify':
            manifest = verify(directory)
            result = {'status': 'verified', 'version': manifest['version'], 'tables': len(manifest['counts']), 'files': len(manifest['files'])}
        else:
            result = restore_check(directory)
    else:
        from linux_setup import operation_lock
        with operation_lock(args.root):
            installation = Installation(args.root)
            if args.action == 'backup':
                result = installation.backup(args.output_dir)
            elif args.action == 'resume':
                result = installation.resume()
            else:
                from linux_backup_schedule import schedule, unschedule
                result = schedule(installation, args.output_dir, args.at) if args.action == 'schedule' else unschedule(installation)
    print(json.dumps(result))


if __name__ == '__main__':
    try:
        main()
    except (ValueError, RuntimeError, OSError, KeyError, subprocess.SubprocessError) as error:
        print('OwnDesk: ' + str(error), file=sys.stderr)
        raise SystemExit(1)
