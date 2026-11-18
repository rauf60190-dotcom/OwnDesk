"""Backup selection and quarantine-only recovery protocol for the setup window."""
from dataclasses import dataclass
from pathlib import Path
import re
import sys

from windows_update_ui import run_process, UpdateError


@dataclass
class CheckedBackup:
    directory: str = ''
    result: dict | None = None

    def clear(self):
        self.directory, self.result = '', None

    def accept(self, directory, result):
        if (result.get('status') != 'backup-verified' or result.get('kind') not in {'portal', 'organization'}
                or not isinstance(result.get('manifest_sha256'), str)
                or not re.fullmatch(r'[a-f0-9]{64}', result['manifest_sha256'])):
            raise ValueError('Проверка копии не подтверждена.')
        self.directory, self.result = str(Path(directory).absolute()), result

    def matches(self, directory):
        return self.result is not None and self.directory == str(Path(directory).absolute())


def run(directory, action, report, manifest_sha256=''):
    if action not in {'verify', 'recover'}: raise ValueError('Неизвестное действие восстановления.')
    path = Path(directory)
    if not directory or not path.is_dir() or not (path / 'manifest.json').is_file():
        raise ValueError('Выберите каталог завершённой резервной копии с manifest.json.')
    args = [str(Path(sys.executable).with_name('python.exe')), '-B', str(Path(__file__).with_name('windows_recovery.py')),
            action, '--backup', str(path.absolute())]
    if action == 'recover':
        if not re.fullmatch(r'[a-f0-9]{64}', manifest_sha256):
            raise ValueError('Сначала проверьте выбранную копию.')
        args += ['--manifest-sha256', manifest_sha256]
    try:
        result = run_process(args, 'backup-verified' if action == 'verify' else 'verified', report)
    except UpdateError as error:
        raise UpdateError('Копия не восстановлена. Проверьте сообщение в журнале; рабочие данные не заменялись.', error.details) from None
    if action == 'recover' and (result.get('quarantine') is not True or result.get('external_services_started') is not False
            or result.get('database_stopped') is not True or result.get('temporary_service_removed') is not True
            or result.get('keys_preserved') is not True or result.get('original_config_protected') is not True):
        raise UpdateError('Не удалось подтвердить изоляцию и завершение восстановления.')
    return result
