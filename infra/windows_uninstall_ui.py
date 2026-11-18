"""GUI subprocess protocol for removing an organization service, never the central portal."""
from pathlib import Path
import re
import sys
from windows_update_ui import paths,run_process,UpdateError

def run(action,report,checked='',confirmation=''):
    if action not in {'check','remove'}:raise ValueError('Неизвестное действие удаления.')
    app,_=paths('OwnDeskServer')
    python=app/'runtime/venv/Scripts/python.exe'
    if not python.is_file():python=Path(sys.executable).with_name('python.exe')
    command=[str(python),'-B',str(Path(__file__).with_name('windows_uninstall.py')),action]
    if action=='remove':
        if not re.fullmatch(r'[a-f0-9]{64}',checked) or confirmation!='OwnDeskServer':
            raise ValueError('Сначала проверьте службу и введите OwnDeskServer.')
        command+=['--expected-check',checked,'--confirm-service',confirmation]
    try:result=run_process(command,'uninstall-ready' if action=='check' else 'service-unregistered',report)
    except UpdateError as error:
        raise UpdateError('Операция не подтверждена. Проверьте журнал; файлы данных автоматически не удаляются.',error.details) from None
    if action=='remove' and (result.get('data_preserved') is not True or result.get('program_files_preserved') is not True):
        raise ValueError('Сохранение данных и файлов программы не подтверждено.')
    return result
