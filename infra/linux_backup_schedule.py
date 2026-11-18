"""Opt-in systemd timers and boot recovery for Linux backups."""
from pathlib import Path
import re
import subprocess
import tempfile
import os

SYSTEM = Path('/etc/systemd/system')


def quote(value):
    value = str(value)
    if any(ord(char) < 32 or ord(char) == 127 for char in value):
        raise ValueError('Control characters are not allowed in unit arguments')
    return '"' + value.replace('\\', '\\\\').replace('"', '\\"').replace('%', '%%') + '"'


def names(installation):
    return 'owndesk-backup-' + installation.project.removeprefix('owndesk-')


def marker(installation):
    return '# OwnDesk managed backup ' + installation.project + '\n'


def unit_contents(installation, directory, at):
    if not re.fullmatch(r'(?:[01][0-9]|2[0-3]):[0-5][0-9]', at):
        raise ValueError('Use local server time HH:MM, for example 02:30')
    name = names(installation)
    command = ':/usr/bin/python3 ' + quote(Path(__file__).with_name('linux_backup.py'))
    root = ' --root ' + quote(installation.root)
    header = marker(installation)
    recover = header + '[Unit]\nDescription=OwnDesk interrupted backup recovery\nRequires=docker.service\nAfter=docker.service\nBefore=' + name + '.service\n\n[Service]\nType=oneshot\nUMask=0077\nTimeoutStartSec=10min\nExecStart=' + command + ' resume' + root + '\n\n[Install]\nWantedBy=multi-user.target\n'
    service = header + '[Unit]\nDescription=OwnDesk verified cold backup\nRequires=docker.service ' + name + '-resume.service\nAfter=docker.service ' + name + '-resume.service\n\n[Service]\nType=oneshot\nUMask=0077\nTimeoutStartSec=2h\nTimeoutStopSec=2min\nExecStart=' + command + ' backup' + root + ' --output-dir ' + quote(directory) + '\nExecStopPost=' + command + ' resume' + root + '\n'
    timer = header + '[Unit]\nDescription=OwnDesk daily backup timer\n\n[Timer]\nOnCalendar=*-*-* ' + at + ':00\nPersistent=true\nRandomizedDelaySec=5min\nUnit=' + name + '.service\n\n[Install]\nWantedBy=timers.target\n'
    return {name + '-resume.service': recover, name + '.service': service, name + '.timer': timer}


def owned_files(installation):
    name = names(installation)
    paths = [SYSTEM / (name + ending) for ending in ('-resume.service', '.service', '.timer')]
    for path in paths:
        if path.is_symlink() or (path.exists() and not path.read_text().startswith(marker(installation))):
            raise ValueError('Refusing to replace an unrelated systemd unit')
    return paths


def schedule(installation, directory, at):
    from linux_backup import private_directory, no_links
    directory = no_links(directory)
    if directory.is_relative_to(installation.root) or installation.root.is_relative_to(directory):
        raise ValueError('Backup storage must be separate from the installation')
    paths = owned_files(installation)
    contents = unit_contents(installation, directory, at)
    if os.name == 'posix':
        for filename in ('linux_backup.py', 'linux_backup_worker.py', 'linux_backup_schedule.py', 'linux_setup.py'):
            path = Path(__file__).with_name(filename)
            for item in [path, *path.parents]:
                info = item.stat()
                if item.is_symlink() or info.st_uid != 0 or info.st_mode & 0o022:
                    raise ValueError('Scheduled backup code must be root-owned and not writable by other users')
    directory = private_directory(directory, create=True)
    # Validate all units together before replacing anything in /etc/systemd/system.
    stage = Path(tempfile.mkdtemp(prefix='backup-units-', dir=installation.state))
    try:
        staged = []
        for filename, content in contents.items():
            path = stage / filename
            path.write_text(content)
            staged.append(str(path))
        subprocess.run(['systemd-analyze', 'verify', *staged], check=True, capture_output=True)
    finally:
        for path in stage.iterdir():
            path.unlink()
        stage.rmdir()
    for path in paths:
        temporary = path.with_suffix(path.suffix + '.pending')
        if temporary.exists() or temporary.is_symlink():
            raise ValueError('A pending unit must be inspected first')
    for path in paths:
        temporary = path.with_suffix(path.suffix + '.pending')
        with temporary.open('x') as stream:
            stream.write(contents[path.name])
        temporary.chmod(0o644)
        temporary.replace(path)
    subprocess.run(['systemctl', 'daemon-reload'], check=True, capture_output=True)
    name = names(installation)
    subprocess.run(['systemctl', 'enable', name + '-resume.service'], check=True, capture_output=True)
    subprocess.run(['systemctl', 'enable', '--now', name + '.timer'], check=True, capture_output=True)
    return {'status': 'scheduled', 'timer': name + '.timer', 'at_server_local_time': at, 'directory': str(directory)}


def unschedule(installation):
    paths = owned_files(installation)
    name = names(installation)
    # Removing a schedule never kills an in-flight backup or deletes existing copies.
    subprocess.run(['systemctl', 'disable', '--now', name + '.timer'], check=True, capture_output=True)
    subprocess.run(['systemctl', 'disable', name + '-resume.service'], check=True, capture_output=True)
    for path in paths:
        path.unlink(missing_ok=True)
    subprocess.run(['systemctl', 'daemon-reload'], check=True, capture_output=True)
    return {'status': 'unscheduled', 'backups_deleted': False}
