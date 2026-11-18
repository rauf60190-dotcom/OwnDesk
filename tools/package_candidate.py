"""Package tracked source and a built frontend into a new, unsigned CI candidate."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess
from zipfile import ZipFile, ZipInfo, ZIP_DEFLATED

from prepare_source_review import ROOT, excluded


def git(*args):
    return subprocess.check_output(['git', '-C', str(ROOT), *args])


def package(output):
    if output.exists():
        raise ValueError('Use a fresh output directory')
    if git('status', '--porcelain', '--untracked-files=no').strip():
        raise ValueError('Tracked source must match the committed revision')
    commit = git('rev-parse', 'HEAD').decode().strip()
    version = (ROOT / 'VERSION').read_text().strip()
    if not re.fullmatch(r'\d+\.\d+\.\d+', version):
        raise ValueError('Invalid VERSION')
    files = {}
    for entry in git('ls-files', '--stage', '-z').split(b'\0'):
        if not entry:
            continue
        header, raw_name = entry.split(b'\t', 1)
        mode, _, stage = header.decode().split()
        name = raw_name.decode()
        relative = Path(name)
        path = ROOT / relative
        if stage != '0' or mode not in {'100644', '100755'} or excluded(relative) or path.is_symlink():
            raise ValueError('Unexpected tracked entry: ' + name)
        files[name] = (path, int(mode, 8))
    dist = ROOT / 'frontend/dist'
    if not (dist / 'index.html').is_file():
        raise ValueError('Build frontend/dist before packaging')
    for path in dist.rglob('*'):
        if path.is_symlink():
            raise ValueError('Links are not allowed in frontend/dist')
        if path.is_file():
            files[path.relative_to(ROOT).as_posix()] = (path, 0o100644)
    total = sum(path.stat().st_size for path, _ in files.values())
    if len(files) > 5000 or total > 128 * 1024 * 1024:
        raise ValueError('Candidate exceeds bootstrap limits')
    output.mkdir(parents=True)
    archive = output / 'OwnDesk.zip'
    with ZipFile(archive, 'x', compression=ZIP_DEFLATED, compresslevel=8) as zipped:
        for name, (path, mode) in sorted(files.items()):
            info = ZipInfo('OwnDesk/' + name, date_time=(2026, 1, 1, 0, 0, 0))
            info.create_system = 3
            info.external_attr = mode << 16
            info.compress_type = ZIP_DEFLATED
            zipped.writestr(info, path.read_bytes(), compresslevel=8)
    with ZipFile(archive) as zipped:
        if zipped.testzip() is not None:
            raise ValueError('Candidate ZIP integrity failure')
    metadata = {'version': version, 'sha256': hashlib.sha256(archive.read_bytes()).hexdigest(),
                'size': archive.stat().st_size, 'source_commit': commit,
                'candidate_only': True, 'public_release_approved': False,
                'files': len(files), 'expanded_bytes': total,
                'inputs': {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
                           for name in ['frontend/pnpm-lock.yaml', 'backend/requirements.txt']}}
    (output / 'release.json').write_text(json.dumps(metadata, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(metadata, indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True, type=Path)
    package(parser.parse_args().output)
