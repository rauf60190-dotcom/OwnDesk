"""Consistent online backups for native installations with managed PostgreSQL."""
import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import sys
import time

CONFIG_FILES = ('secrets.json', 'runtime-profile.json', 'portal-connection.json',
                'integrations.json', 'cloudflare.json', 'root-tunnel.token', 'mail-settings.json')


def safe_file(root, relative):
    if root.is_symlink() or root.is_junction():
        raise ValueError('Backup roots must not use links')
    if not isinstance(relative, str) or not relative or '\\' in relative or ':' in relative:
        raise ValueError('Invalid backup file path')
    parts = PurePosixPath(relative)
    if parts.is_absolute() or any(p in {'..', '.'} for p in parts.parts) or parts.as_posix() != relative:
        raise ValueError('Invalid backup file path')
    path = root
    for part in parts.parts:
        path = path / part
        if path.is_symlink() or path.is_junction():
            raise ValueError('Backup files must not use links')
    if not path.resolve().is_relative_to(root.resolve()) or not path.is_file() or path.stat().st_nlink != 1:
        raise ValueError('Expected a regular private file')
    return path


def fingerprint(path):
    with path.open('rb') as stream:
        digest = hashlib.file_digest(stream, 'sha256').hexdigest()
    return {'size': path.stat().st_size, 'sha256': digest}


def verify(directory):
    manifest = json.loads(safe_file(directory, 'manifest.json').read_text(encoding='utf-8'))
    if manifest.get('format') != 1 or manifest.get('kind') not in {'portal', 'organization'}:
        raise ValueError('Unsupported backup manifest')
    files = manifest.get('files')
    if not isinstance(files, dict) or not {'database.dump', 'config/secrets.json', 'config/runtime-profile.json'} <= files.keys():
        raise ValueError('Incomplete backup manifest')
    for relative, expected in files.items():
        if (not isinstance(expected, dict) or type(expected.get('size')) is not int or expected['size'] < 0
                or not isinstance(expected.get('sha256'), str)
                or not re.fullmatch(r'[a-f0-9]{64}', expected['sha256'])):
            raise ValueError('Invalid backup file metadata')
        if fingerprint(safe_file(directory, relative)) != expected:
            raise ValueError('Backup file checksum differs: ' + relative)
    return manifest


def run_dump(binary, archive, snapshot, env):
    with archive.with_suffix('.log').open('ab') as log:
        result = subprocess.run([str(binary), '-w', '-Fc', '--no-owner', '--no-privileges',
            '--exclude-table=public._owndesk_restore_receipt', '--snapshot', snapshot, '-f', str(archive)],
            env=env, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
    if result.returncode:
        raise RuntimeError('Database backup failed; inspect the private database.log')


def backup(data, destination, version):
    """Destination must already be private and empty; manifest marks successful completion."""
    if not destination.is_dir() or any(destination.iterdir()):
        raise ValueError('Backup requires an existing, private, empty destination')
    if data.resolve().is_relative_to(destination.resolve()) or destination.resolve().is_relative_to(data.resolve()):
        raise ValueError('Backup destination must be separate from live data')
    configs = {name: safe_file(data, name).read_bytes() for name in CONFIG_FILES if (data / name).exists()}
    profile = json.loads(configs['runtime-profile.json'])
    secret = json.loads(configs['secrets.json'])
    if profile.get('public_hostname') == 'owndesk.work':
        from portal_runtime import profile_for
        kind = 'portal'
    else:
        from native_runtime import profile_for
        kind = 'organization'
    profile_for(data)
    pg = profile.get('postgres')
    if not pg:
        raise ValueError('This backup command requires a managed native PostgreSQL installation')
    for key in ('database', 'role'):
        if not re.fullmatch(r'[a-z][a-z0-9_]{0,62}', pg[key]):
            raise ValueError('Invalid database identifier')
    binaries = Path(pg['bin'])
    if not binaries.is_absolute():
        raise ValueError('Backup requires the absolute managed PostgreSQL binaries path')
    os.environ['PATH'] = str(binaries) + os.pathsep + os.environ.get('PATH', '')
    os.environ.setdefault('PSYCOPG_IMPL', 'python')
    import psycopg
    from psycopg import sql
    files = {}
    def record(relative):
        files[relative] = fingerprint(safe_file(destination, relative))
    (destination / 'config').mkdir()
    for name, content in configs.items():
        (destination / 'config' / name).write_bytes(content)
        record('config/' + name)
    env = {k: v for k, v in os.environ.items() if not k.startswith('PG')}
    env.update(PGHOST='127.0.0.1', PGPORT=str(pg['port']), PGDATABASE=pg['database'],
               PGUSER=pg['role'], PGPASSWORD=secret['POSTGRES_APP_PASSWORD'], PGCONNECT_TIMEOUT='5')
    with psycopg.connect(host='127.0.0.1', port=pg['port'], dbname=pg['database'], user=pg['role'],
                          password=secret['POSTGRES_APP_PASSWORD'], connect_timeout=5) as connection:
        connection.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ')
        connection.execute("SET LOCAL lock_timeout = '5s'")
        # Retention locks the same rows before deleting files. Hold shared row locks until the copies finish.
        attachments = connection.execute("SELECT id, object_key, size FROM attachments_attachment WHERE state='ready' ORDER BY id FOR SHARE").fetchall()
        snapshot = connection.execute('SELECT pg_export_snapshot()').fetchone()[0]
        tables = [r[0] for r in connection.execute("SELECT tablename FROM pg_tables WHERE schemaname='public' AND tablename <> '_owndesk_restore_receipt' ORDER BY tablename")]
        counts = {table: connection.execute(sql.SQL('SELECT count(*) FROM {}').format(sql.Identifier(table))).fetchone()[0] for table in tables}
        started = time.time()
        binary = binaries / ('pg_dump.exe' if os.name == 'nt' else 'pg_dump')
        run_dump(binary, destination / 'database.dump', snapshot, env)
        record('database.dump')
        for identity, key, size in attachments:
            source = safe_file(data / 'private-attachments', key)
            relative = 'private-attachments/' + key
            target = destination / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)
            record(relative)
            if files[relative]['size'] != size:
                raise RuntimeError('Attachment size differs from database: ' + str(identity))
    current_configs = {name: safe_file(data, name).read_bytes() for name in CONFIG_FILES if (data / name).exists()}
    if current_configs != configs:
        raise RuntimeError('Private configuration changed during backup; retry with stable configuration')
    manifest = {'format': 1, 'kind': kind, 'version': version, 'snapshot_started_at': started,
        'completed_at': time.time(), 'counts': counts, 'ready_attachments': len(attachments), 'files': files}
    temporary = destination / 'manifest.pending'
    temporary.write_text(json.dumps(manifest, indent=2), encoding='utf-8')
    temporary.replace(destination / 'manifest.json')
    return verify(destination)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=('backup', 'verify'))
    parser.add_argument('--data', type=Path)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    if args.action == 'backup':
        if args.data is None:
            parser.error('--data is required for backup')
        if os.name != 'nt':
            raise RuntimeError('The managed Windows backup CLI has not been accepted on Linux')
        from windows_service import admin_required, secure_directory
        admin_required()
        root = Path(os.environ['ProgramData']) / 'OwnDesk/Backups'
        output = args.output.absolute()
        if output.parent != root or output.exists():
            raise ValueError('Choose a new direct subdirectory of ProgramData/OwnDesk/Backups')
        secure_directory(root, admin_only=True)
        secure_directory(output, admin_only=True)
        data = args.data.resolve()
        # Record the deployed version, which may differ from the version of these backup tools.
        expected = Path(os.environ['ProgramData']) / 'OwnDesk'
        if data not in {expected / 'Portal', expected / 'Server'}:
            raise ValueError('Choose the managed Portal or Server data directory')
        app = Path(os.environ['ProgramFiles']) / 'OwnDesk' / data.name
        manifest = backup(data, output, (app / 'VERSION').read_text().strip())
    else:
        manifest = verify(args.output.resolve())
    print(json.dumps({'status': 'verified', 'kind': manifest['kind'], 'version': manifest['version'],
                      'files': len(manifest['files']), 'attachments': manifest['ready_attachments']}))


if __name__ == '__main__':
    try:
        main()
    except (ValueError, RuntimeError, OSError, KeyError) as error:
        print('OwnDesk: ' + str(error), file=sys.stderr)
        raise SystemExit(1)
