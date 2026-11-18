"""Verified dependencies for a native Windows server; no portal credentials here."""
from hashlib import file_digest, sha256
import json
import os
from pathlib import Path, PurePosixPath
import platform
import shutil
import subprocess
import sys
import tempfile
from urllib.request import urlopen
from zipfile import ZipFile

ROOT = Path(__file__).resolve().parents[1]


def digest(path):
    with path.open('rb') as stream:
        return file_digest(stream, 'sha256').hexdigest()


def download(item, cache, report=print):
    cache.mkdir(parents=True, exist_ok=True)
    target = cache / item['filename']
    if target.exists() and digest(target) == item['sha256']:
        return target
    if not item['url'].startswith('https://'):
        raise ValueError('Dependency URL must use HTTPS')
    partial = target.with_suffix(target.suffix + '.partial')
    report('Скачиваем ' + item['filename'] + '…')
    try:
        with urlopen(item['url'], timeout=60) as response, partial.open('wb') as stream:
            count = 0
            while chunk := response.read(1024 * 1024):
                count += len(chunk)
                if count > item['size']:
                    raise RuntimeError('Unexpected dependency size')
                stream.write(chunk)
        if count != item['size'] or digest(partial) != item['sha256']:
            raise RuntimeError('SHA-256 verification failed: ' + item['filename'])
        os.replace(partial, target)
    finally:
        partial.unlink(missing_ok=True)
    return target


def extract(archive, target, prefixes):
    """Only regular files beneath explicitly selected archive prefixes."""
    with ZipFile(archive) as package:
        for entry in package.infolist():
            name = PurePosixPath(entry.filename)
            if name.is_absolute() or '..' in name.parts or '\\' in entry.orig_filename or ':' in entry.filename or '\x00' in entry.orig_filename:
                raise ValueError('Unsafe dependency archive path')
            if (entry.external_attr >> 16) & 0o170000 == 0o120000:
                raise ValueError('Dependency archive contains a symbolic link')
            if entry.is_dir() or not any(entry.filename.startswith(prefix) for prefix in prefixes):
                continue
            path = target.joinpath(*name.parts)
            path.parent.mkdir(parents=True, exist_ok=True)
            with package.open(entry) as source, path.open('wb') as destination:
                shutil.copyfileobj(source, destination)


def prepare_tools(data, report=print, cache=None):
    if os.name != 'nt' or platform.machine().lower() not in {'amd64', 'x86_64'}:
        raise RuntimeError('Windows x86-64 is required')
    manifest = json.loads((ROOT / 'infra/windows-dependencies.json').read_text())
    tools = data / 'tools'
    tools.mkdir(parents=True, exist_ok=True)
    for key, item in manifest.items():
        target = tools / (key + '-' + item['version'])
        stamp = target / 'verified-files.json'
        if stamp.exists():
            files = json.loads(stamp.read_text())
            if files and all((target / name).is_file() and digest(target / name) == value for name, value in files.items()):
                continue
            raise RuntimeError('Installed dependency changed: ' + key + '. Restore from the verified archive before continuing.')
        archive = download(item, cache or data / 'downloads', report)
        # Interrupted extraction never becomes an executable installation.
        with tempfile.TemporaryDirectory(prefix=key + '-', dir=tools) as temporary:
            staging = Path(temporary)
            if key == 'postgresql':
                extract(archive, staging, ('pgsql/bin/', 'pgsql/lib/', 'pgsql/share/'))
            elif key == 'caddy':
                extract(archive, staging, ('caddy.exe',))
            else:
                shutil.copy2(archive, staging / (key + '.exe'))
            files = {p.relative_to(staging).as_posix(): digest(p) for p in staging.rglob('*') if p.is_file()}
            (staging / 'verified-files.json').write_text(json.dumps(files), encoding='utf-8')
            if target.exists():
                raise RuntimeError('Unverified dependency directory exists: ' + str(target))
            os.replace(staging, target)
    return {
        'postgres_bin': tools / ('postgresql-' + manifest['postgresql']['version']) / 'pgsql/bin',
        'caddy': tools / ('caddy-' + manifest['caddy']['version']) / 'caddy.exe',
        'cloudflared': tools / ('cloudflared-' + manifest['cloudflared']['version']) / 'cloudflared.exe',
        'winsw': tools / ('winsw-' + manifest['winsw']['version']) / 'winsw.exe',
    }


def execute(args, report=print, *, stdin=None):
    with subprocess.Popen([str(arg) for arg in args], stdin=subprocess.PIPE if stdin is not None else subprocess.DEVNULL,
                          stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding='utf-8', errors='replace',
                          creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0,
                          env={**os.environ, 'PYTHONUTF8': '1', 'PYTHONIOENCODING': 'utf-8'}) as child:
        if stdin is not None:
            child.stdin.write(stdin)
            child.stdin.close()
        for line in child.stdout:
            report(line.rstrip())
        if child.wait():
            raise RuntimeError('Preparation command failed; see the setup log')


def prepare_python(app, base_python, report=print):
    venv = app / 'runtime/venv'
    python = venv / 'Scripts/python.exe'
    if not python.exists():
        execute([base_python, '-m', 'venv', venv], report)
    requirements = app / 'backend/requirements.txt'
    signature = sha256(requirements.read_bytes()).hexdigest()
    stamp = venv / 'requirements.sha256'
    if not stamp.exists() or stamp.read_text() != signature:
        report('Устанавливаем Python-зависимости…')
        execute([python, '-m', 'pip', 'install', '--disable-pip-version-check', '-r', requirements], report)
        stamp.write_text(signature)
    return python


def copy_interpreter(source, base, report=print):
    """Keep venv launcher templates; Windows matches ignore patterns without case sensitivity."""
    source = Path(source)
    if not (base / 'python.exe').exists():
        report('Подготавливаем независимую копию Python…')
        base.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix='python-', dir=base.parent) as temporary:
            staging = Path(temporary)
            shutil.copytree(source, staging, dirs_exist_ok=True,
                            ignore=shutil.ignore_patterns('site-packages', '__pycache__', '*.pyc'))
            os.replace(staging, base)
    templates = source / 'Lib/venv/scripts'
    launchers = [p for p in (templates / 'nt/python.exe', templates / 'nt/venvlauncher.exe') if p.exists()]
    if launchers and any(not (base / p.relative_to(source)).exists() for p in launchers):
        shutil.copytree(templates, base / 'Lib/venv/scripts', dirs_exist_ok=True)
