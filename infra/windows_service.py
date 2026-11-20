"""Install one native organization as a least-privilege Windows service."""
import argparse
import ctypes
import json
import os
from pathlib import Path
import shutil
import socket
import struct
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
from urllib.request import Request, urlopen
from uuid import UUID

from windows_setup import ROOT, copy_interpreter, execute, prepare_python, prepare_tools, windows_powershell_environment

SERVICE = 'OwnDeskServer'


def admin_required():
    if os.name != 'nt' or not ctypes.windll.shell32.IsUserAnAdmin():
        raise RuntimeError('Запустите установщик сервера от имени администратора Windows.')


def paths():
    return Path(os.environ['ProgramFiles']) / 'OwnDesk/Server', Path(os.environ['ProgramData']) / 'OwnDesk/Server'


def secure_directory(path, writable=False, *, service_sid='S-1-5-19', shared_parent=False, admin_only=False):
    """Protect each service's private directory; shared product parents are read-only."""
    if service_sid not in {'S-1-5-19', 'S-1-5-20'}:
        raise ValueError('Unsupported service identity')
    if (shared_parent and writable) or (admin_only and (shared_parent or writable)):
        raise ValueError('Invalid directory access combination')
    for ancestor in (path, *path.parents):
        if ancestor.is_symlink() or ancestor.is_junction():
            raise RuntimeError('Каталог установки не должен содержать junction/symlink: ' + str(ancestor))
    path.mkdir(parents=True, exist_ok=True)
    execute(['icacls', path, '/setowner', '*S-1-5-32-544'], lambda _: None)
    args = ['powershell.exe', '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass',
            '-File', ROOT / 'infra/set_directory_acl.ps1', '-Target', path, '-ServiceSid', service_sid]
    if writable:
        args.append('-Writable')
    if shared_parent:
        args.append('-SharedParent')
    if admin_only:
        args.append('-AdminOnly')
    execute(args, lambda _: None)


def service_xml(app, data, python, *, service_id=None, account='LocalService',
                entrypoint='native_runtime.py', display_name='OwnDesk Server', start_mode='Automatic'):
    import re
    service_id = SERVICE if service_id is None else service_id
    if (not re.fullmatch(r'OwnDesk[A-Za-z0-9]{1,60}', service_id)
            or account not in {'LocalService', 'NetworkService'}
            or entrypoint not in {'native_runtime.py', 'portal_runtime.py'}
            or start_mode not in {'Automatic', 'Manual'}):
        raise ValueError('Invalid Windows service configuration')
    service = ET.Element('service')
    values = {'id': service_id, 'name': display_name, 'description': display_name,
              'executable': str(python), 'workingdirectory': str(app),
              'startarguments': subprocess.list2cmdline([str(app / 'infra' / entrypoint), 'run', '--data', str(data)]),
              'stoparguments': subprocess.list2cmdline([str(app / 'infra' / entrypoint), 'stop', '--data', str(data)]),
              'startmode': start_mode, 'delayedAutoStart': str(start_mode == 'Automatic').lower(), 'stoptimeout': '60sec',
              'stopparentprocessfirst': 'true', 'logpath': str(data / 'service-logs')}
    for key, value in values.items():
        ET.SubElement(service, key).text = value
    values_account = account
    account = ET.SubElement(service, 'serviceaccount')
    ET.SubElement(account, 'domain').text = 'NT AUTHORITY'
    ET.SubElement(account, 'user').text = values_account
    ET.SubElement(service, 'onfailure', action='restart', delay='30sec')
    ET.SubElement(service, 'log', mode='roll')
    ET.SubElement(service, 'env', name='PYTHONDONTWRITEBYTECODE', value='1')
    ET.SubElement(service, 'env', name='PYTHONUTF8', value='1')
    return ET.tostring(service, encoding='unicode')


def check_ports():
    for port in (5173, 8000, 55486):
        try:
            with socket.socket() as probe:
                probe.bind(('127.0.0.1', port))
        except OSError:
            raise RuntimeError(f'Порт {port} занят. Остановите прежний сервер OwnDesk перед установкой службы. Данные не изменены.') from None


def prepare_application(app, data, report=print, cache=None, *, service_id=None, service_sid='S-1-5-19'):
    admin_required()
    service_id = SERVICE if service_id is None else service_id
    if sys.version_info[:2] not in {(3, 12), (3, 14)} or struct.calcsize('P') != 8:
        raise RuntimeError('Требуется Python 3.14 x64 из установщика OwnDesk либо прежняя среда Python 3.12 x64.')
    for parent in (app.parent, data.parent):
        secure_directory(parent, shared_parent=True)
    secure_directory(app, service_sid=service_sid)
    secure_directory(data, writable=True, service_sid=service_sid)
    reporter = report

    def report(line):
        with (data / 'setup.log').open('a', encoding='utf-8') as journal:
            journal.write(line + '\n')
        reporter(line)

    marker = app / 'installation.json'
    version = (ROOT / 'VERSION').read_text().strip()
    if marker.exists():
        metadata = json.loads(marker.read_text())
        if metadata != {'service': service_id, 'version': version}:
            raise RuntimeError('Найдена другая версия сервера. Требуется отдельное обновление с резервной копией.')
    elif any(app.iterdir()):
        raise RuntimeError('Каталог сервера не пуст и не содержит метаданных OwnDesk. Файлы оставлены без изменений.')
    else:
        marker.write_text(json.dumps({'service': service_id, 'version': version}))
    report('Копируем программу в защищённый каталог…')
    for name in ('backend', 'infra'):
        shutil.copytree(ROOT / name, app / name, dirs_exist_ok=True,
                        ignore=shutil.ignore_patterns('__pycache__', '*.sqlite3', '.env', '.env.*', '*.log', '*.pyc'))
    shutil.copytree(ROOT / 'frontend/dist', app / 'frontend/dist', dirs_exist_ok=True)
    shutil.copy2(ROOT / 'VERSION', app / 'VERSION')
    base = app / 'runtime/python'
    copy_interpreter(Path(sys.base_prefix), base, report)
    python = prepare_python(app, base / 'python.exe', report)
    tools = prepare_tools(app / 'runtime', report, cache=cache)
    # Windows creates TemporaryDirectory with a private DACL. Atomic publication
    # preserves it, so normalize the published code tree before LocalService runs it.
    report('Проверяем доступ службы к установленным компонентам…')
    execute(['icacls', app, '/reset', '/T', '/Q'], lambda _: None)
    secure_directory(app, service_sid=service_sid)
    return python, tools, report


def install(code='', report=print, code_provider=None):
    admin_required()
    app, data = paths()
    exists = subprocess.run(['sc.exe', 'query', SERVICE], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                            creationflags=subprocess.CREATE_NO_WINDOW).returncode == 0
    if exists:
        raise RuntimeError('Служба OwnDesk уже установлена. Используйте управление службой; обновление выполняется отдельно.')
    check_ports()
    if (ROOT / '.runtime/demo.sqlite3').exists() and not (data / 'runtime-profile.json').exists():
        raise RuntimeError('В этом дистрибутиве есть локальная база. Автоматический перенос пока не поддерживается. Сохраните её и используйте чистый дистрибутив для новой организации.')
    python, tools, report = prepare_application(app, data, report)
    state_file = data / 'portal-connection.json'
    pending = json.loads(state_file.read_text()) if state_file.exists() else {}
    if not pending.get('id'):
        if code_provider is not None:
            code = code_provider()
        if code is None:
            raise RuntimeError('Подключение отменено. Зависимости сохранены для повторного запуска.')
        if not (code == '' and pending.get('code')) and not 32 <= len(code) <= 128:
            raise RuntimeError('Нужен свежий код подключения из кабинета owndesk.work. Зависимости уже сохранены; повторите установку.')
    # The preparation helper receives the pairing secret through stdin, never argv or logs.
    execute([python, app / 'infra/native_runtime.py', 'prepare', '--data', data,
             '--pair-code-stdin', '--protocol', 'http2', '--postgres-bin', tools['postgres_bin'],
             '--caddy', tools['caddy'], '--cloudflared', tools['cloudflared']], report, stdin=code + '\n')
    host = app / 'OwnDeskService.exe'
    shutil.copy2(tools['winsw'], host)
    (app / 'OwnDeskService.xml').write_text(service_xml(app, data, python), encoding='utf-8')
    report('Регистрируем службу OwnDesk под LocalService…')
    execute([host, 'install'], report)
    # Fail closed if the wrapper did not honor the restricted service identity.
    result = subprocess.run(['powershell.exe', '-NoProfile', '-NonInteractive', '-Command',
        f"(Get-CimInstance Win32_Service -Filter \"Name='{SERVICE}'\").StartName"], capture_output=True, text=True,
        creationflags=subprocess.CREATE_NO_WINDOW, check=True, env=windows_powershell_environment())
    if result.stdout.strip().lower() != r'nt authority\localservice':
        execute([host, 'uninstall'], report)
        raise RuntimeError('Учётная запись службы не подтверждена; служба удалена, данные сохранены.')
    execute([host, 'start'], report)
    report('Ожидаем PostgreSQL, HTTPS и готовности мастера…')
    return wait_ready(app, data)


def wait_ready(app, data, timeout=180):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            status = json.loads((data / 'portal-connection.json.status.json').read_text())
            if status.get('state') == 'active' and 0 <= time.time() - float(status.get('checked_at', 0)) < 90:
                import urllib.parse
                profile = json.loads((data / 'runtime-profile.json').read_text())
                state = json.loads((data / 'portal-connection.json').read_text())
                hostname = 'org-' + UUID(str(state['id'])).hex + '.owndesk.work'
                if profile['public_hostname'] != hostname:
                    raise RuntimeError('Адрес профиля не совпадает с подключённой установкой.')
                origin = 'https://' + hostname
                request = Request(origin + '/api/v1/health/live', headers={
                    'User-Agent':'OwnDesk/' + (ROOT / 'VERSION').read_text().strip() + ' Installer',
                    'Accept':'application/json'})
                with urlopen(request, timeout=3) as response:
                    if response.status != 200 or response.geturl() != origin + '/api/v1/health/live' or json.loads(response.read(4096)).get('status') != 'alive':
                        raise OSError('Public server is not ready')
                setup = data / 'installation-code.txt'
                return origin + ('/#' + urllib.parse.urlencode({'setup': setup.read_text().strip()}) if setup.exists() else '/')
        except (OSError, ValueError, KeyError, TypeError):
            pass
        time.sleep(2)
    raise RuntimeError('Служба установлена, но публичный адрес ещё не готов. Откройте журнал; повторно создавать организацию не нужно.')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=('install', 'start', 'stop', 'status', 'setup-link'))
    args = parser.parse_args()
    admin_required()
    app, data = paths()
    if args.action == 'install':
        import getpass
        print(install(code_provider=lambda: getpass.getpass('Свежий код подключения: ').strip()))
    elif args.action == 'setup-link':
        print(wait_ready(app, data, 5))
    else:
        execute([app / 'OwnDeskService.exe', 'stopwait' if args.action == 'stop' else args.action])


if __name__ == '__main__':
    try:
        main()
    except (RuntimeError, OSError, ValueError, subprocess.CalledProcessError) as error:
        print('OwnDesk: ' + str(error), file=sys.stderr)
        raise SystemExit(1)
