"""Non-visual update selection and subprocess protocol used by the Windows setup window."""
from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
import subprocess

SERVICES = {'Организация': 'OwnDeskServer', 'Портал владельцев': 'OwnDeskPortal'}


@dataclass(frozen=True)
class Selection:
    service: str
    archive: str = ''
    sha256: str = ''

    def signature(self):
        return self.service, str(Path(self.archive).absolute()), self.sha256.strip().lower()

    def validate(self):
        if self.service not in SERVICES.values():
            raise ValueError('Выберите службу OwnDesk.')
        if not self.archive or not Path(self.archive).is_file() or Path(self.archive).suffix.lower() != '.zip':
            raise ValueError('Выберите существующий ZIP-архив OwnDesk.')
        if not re.fullmatch(r'[a-f0-9]{64}', self.sha256.strip().lower()):
            raise ValueError('Вставьте SHA-256 из кабинета: 64 символа.')


@dataclass
class CheckedUpdate:
    selection: tuple | None = None
    result: dict | None = None

    def clear(self):
        self.selection = self.result = None

    def accept(self, selection, result):
        if (result.get('status') != 'compatible-maintenance-update'
                or any(not isinstance(result.get(key), str) or not re.fullmatch(r'[0-9]+\.[0-9]+\.[0-9]+', result[key])
                       for key in ('installed', 'candidate'))):
            raise ValueError('Совместимость обновления не подтверждена.')
        self.selection, self.result = selection.signature(), result

    def matches(self, selection):
        return self.selection is not None and self.selection == selection.signature()


def paths(service):
    if service not in SERVICES.values():
        raise ValueError('Неизвестная служба OwnDesk.')
    name = 'Portal' if service == 'OwnDeskPortal' else 'Server'
    return Path(os.environ['ProgramFiles']) / 'OwnDesk' / name, Path(os.environ['ProgramData']) / 'OwnDesk' / name


def command(selection, action, transaction='', python=None):
    if action not in {'check', 'apply', 'recover'} or selection.service not in SERVICES.values():
        raise ValueError('Неизвестное действие обновления.')
    if python is None:
        app, _ = paths(selection.service)
        python = app / 'runtime/venv/Scripts/python.exe'
    result = [str(python), '-B', str(Path(__file__).with_name('windows_update.py')),
              action, '--service', selection.service, '--progress']
    if action == 'recover':
        if not re.fullmatch(r'[a-f0-9]{32}', transaction.strip()):
            raise ValueError('Введите идентификатор операции: 32 символа из журнала обновления.')
        result += ['--transaction', transaction.strip()]
    else:
        selection.validate()
        result += ['--archive', str(Path(selection.archive).absolute()), '--sha256', selection.sha256.strip().lower()]
    return result


class UpdateError(RuntimeError):
    def __init__(self, message, details='', transaction=''):
        super().__init__(message)
        self.details, self.transaction = details, transaction


def failure(details):
    if 'previous code restarted' in details:
        message = 'Обновление не завершилось. Прежняя программа запущена с текущей рабочей базой.'
    elif 'Rollback could not be verified' in details:
        message = 'Запуск не подтверждён. Используйте «Восстановить запуск» с идентификатором операции.'
    elif 'Recover unfinished transaction first:' in details:
        message = 'Найдена незавершённая операция. Используйте «Восстановить запуск» с её идентификатором.'
    elif 'SHA-256' in details:
        message = 'Контрольная сумма архива не совпала. Проверьте архив и SHA-256 из кабинета.'
    elif 'Maintenance update refused' in details:
        message = 'Эта сборка меняет приложение или зависимости. Автоматическое обновление такого типа пока не поддерживается.'
    elif 'Update must be newer' in details:
        message = 'Выбранная версия не новее установленной. Проверьте выпуск в кабинете.'
    else:
        message = 'Операция не завершена. Подробности показаны в журнале окна.'
    match = re.search(r'(?:--transaction|transaction(?: first:)?)\s+([a-f0-9]{32})\b', details)
    return UpdateError(message, details, match.group(1) if match else '')


def run(selection, action, report, transaction=''):
    args = command(selection, action, transaction)
    expected = {'check': 'compatible-maintenance-update', 'apply': 'complete', 'recover': 'rolled-back'}[action]
    return run_process(args, expected, report)


def run_process(args, expected, report):
    if not Path(args[0]).is_file():
        raise UpdateError('Установленная служба не найдена. Сначала установите рабочий сервер.')
    result, lines = None, []
    with subprocess.Popen(args, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding='utf-8', errors='replace',
            env={**os.environ, 'PYTHONUTF8': '1', 'PYTHONIOENCODING': 'utf-8', 'PYTHONDONTWRITEBYTECODE': '1'},
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0) as child:
        for line in child.stdout:
            text = line.strip()
            if not text: continue
            try:
                value = json.loads(text)
            except ValueError:
                value = None
            if isinstance(value, dict) and value.get('event') == 'progress':
                report(str(value.get('message', '')))
            elif isinstance(value, dict) and 'status' in value:
                result = value
            else:
                lines.append(text)
                lines = lines[-30:]
                report(text)
        code = child.wait()
    if code or not result or result.get('status') != expected:
        raise failure('\n'.join(lines) or 'Updater did not return a confirmed result')
    return result
