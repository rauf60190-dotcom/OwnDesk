"""Filesystem-only helper run in a temporary, network-isolated container."""
import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat


def regular(root, name):
    path = PurePosixPath(name)
    if (not name or path.is_absolute() or path.as_posix() != name or '..' in path.parts
            or '\\' in name or ':' in name):
        raise ValueError('Invalid backup file name')
    target = root
    for part in path.parts:
        target = target / part
        if target.is_symlink():
            raise ValueError('Links are not backup files')
    info = target.stat()
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        raise ValueError('Expected a single regular file')
    return target


def fingerprint(path):
    with path.open('rb') as stream:
        return {'size': path.stat().st_size, 'sha256': hashlib.file_digest(stream, 'sha256').hexdigest()}


def export():
    source, destination = Path('/source'), Path('/backup/data')
    destination.mkdir(mode=0o700)
    names = ['secrets.json', 'runtime-profile.json', 'portal-connection.json']
    profile = json.loads(regular(source, 'runtime-profile.json').read_text())
    connection = json.loads(regular(source, 'portal-connection.json').read_text())
    from uuid import UUID
    if profile.get('public_hostname') != 'org-' + UUID(connection['id']).hex + '.owndesk.work':
        raise ValueError('Only organization data can be copied')
    for name in ('integrations.json', 'installation-code.txt'):
        if (source / name).exists():
            names.append(name)
    media = source / 'private-attachments'
    if media.is_symlink():
        raise ValueError('Media directory must not be a link')
    if media.exists():
        for path in media.rglob('*'):
            if path.is_symlink():
                raise ValueError('Media links are not supported')
            if path.is_file():
                names.append(path.relative_to(source).as_posix())
            elif not path.is_dir():
                raise ValueError('Unexpected media object')
    result = {}
    for name in names:
        path = regular(source, name)
        target = destination / name
        target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        shutil.copyfile(path, target)
        target.chmod(0o600)
        result['data/' + name] = fingerprint(target)
    return result


def restore():
    source, destination = Path('/backup'), Path('/restored')
    manifest = json.loads(regular(source, 'manifest.json').read_text())
    count = media = 0
    for name, expected in manifest['files'].items():
        if not name.startswith('data/'):
            continue
        path = regular(source, name)
        if fingerprint(path) != expected:
            raise ValueError('Backup file changed during restore')
        target = destination / name.removeprefix('data/')
        target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        shutil.copyfile(path, target)
        target.chmod(0o600)
        if fingerprint(target) != expected:
            raise ValueError('Restored file differs')
        count += 1
        media += name.startswith('data/private-attachments/')
    for path in [*destination.rglob('*'), destination]:
        os.chown(path, 10001, 10001)
    destination.chmod(0o700)
    return {'files': count, 'media': media, 'keys_preserved': True}


def main():
    os.umask(0o077)
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=('export', 'restore', 'prepare-pg'))
    parser.add_argument('--uid', type=int)
    parser.add_argument('--gid', type=int)
    args = parser.parse_args()
    if args.action == 'prepare-pg':
        if not args.uid or not args.gid or not 0 < args.uid < 65536 or not 0 < args.gid < 65536:
            raise ValueError('Invalid PostgreSQL identity')
        os.chown('/pgdata', args.uid, args.gid)
        os.chmod('/pgdata', 0o700)
        result = {'prepared': True}
    else:
        result = export() if args.action == 'export' else restore()
    print(json.dumps(result))


if __name__ == '__main__':
    main()
