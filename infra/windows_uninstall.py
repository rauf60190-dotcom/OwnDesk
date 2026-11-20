"""Remove only a verified OwnDesk service registration; retain all program and data files."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
from uuid import uuid4

import windows_service as service
import windows_update as update
from windows_setup import windows_powershell_environment
from native_runtime import save_private

CONFIGS=('runtime-profile.json','secrets.json','portal-connection.json','mail-settings.json')
REQUIRED_CONFIGS=set(CONFIGS[:3])
PENDING={'stopping','switching','starting','rolling-back','recovery-required'}
TRUSTED_WRAPPER_SHA=json.loads((Path(__file__).parent/'windows-dependencies.json').read_text())['winsw']['sha256']

def registration(name):
    if not re.fullmatch(r'OwnDesk[A-Za-z0-9]{1,60}',name):raise ValueError('Invalid service name')
    result=subprocess.run(['powershell.exe','-NoProfile','-NonInteractive','-Command',
        f"Get-CimInstance Win32_Service -Filter \"Name='{name}'\" | Select-Object Name,State,StartName,PathName | ConvertTo-Json -Compress"],
        capture_output=True,text=True,check=True,creationflags=subprocess.CREATE_NO_WINDOW,
        env=windows_powershell_environment())
    return json.loads(result.stdout) if result.stdout.strip() else None

def private_fingerprints(data):
    result={}
    for name in CONFIGS:
        target=data/name
        update.no_links(target)
        if target.exists():
            if not target.is_file() or target.stat().st_nlink!=1:raise ValueError('Expected a regular private configuration file')
            result[name]=update.sha(target)
    if not REQUIRED_CONFIGS<=result.keys():raise ValueError('Private installation files are missing; automatic removal refused')
    return result

def inspect(spec):
    if registration(spec.name) is None:
        return {'status':'not-installed','service':spec.name,'data':str(spec.data),'data_deleted':False}
    actual=update.query(spec)
    if actual['State'] not in {'Running','Stopped'}:raise RuntimeError('Дождитесь завершения запуска или остановки службы.')
    app,xml=update.installed(spec)
    digest=update.sha(spec.host)
    if digest!=TRUSTED_WRAPPER_SHA:raise ValueError('Installed service wrapper does not match the trusted WinSW release')
    version=(app/'VERSION').read_text().strip()
    if not re.fullmatch(r'\d+\.\d+\.\d+',version):raise ValueError('Invalid installed version')
    result={'status':'uninstall-ready','service':spec.name,'host':str(spec.host),'app':str(app),'data':str(spec.data),
        'state':actual['State'],'version':version,'host_sha256':digest,
        'xml_sha256':hashlib.sha256(xml.encode()).hexdigest(),'config_fingerprints':private_fingerprints(spec.data),
        'fresh_backup_required':actual['State']=='Running','data_deleted':False,'program_files_deleted':False}
    result['check_sha256']=hashlib.sha256(json.dumps(result,sort_keys=True).encode()).hexdigest()
    return result

def remove(spec,expected,confirmation,report=print):
    service.admin_required()
    if spec.account!='LocalService':raise ValueError('Удаление центрального портала этим мастером не поддерживается.')
    if confirmation!=spec.name or not re.fullmatch(r'[a-f0-9]{64}',expected):
        raise ValueError('Повторите проверку и введите точное имя службы для подтверждения.')
    sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'backend'))
    from local_snapshot import local_runtime_lock
    from native_backup import backup,verify
    updates=Path(os.environ['ProgramData'])/'OwnDesk/ServiceUpdates'/spec.name
    service.secure_directory(updates,admin_only=True)
    with local_runtime_lock(updates/'updater'):
        for path in updates.glob('*/update.json'):
            update.no_links(path)
            if json.loads(path.read_text()).get('phase') in PENDING:
                raise RuntimeError('Сначала завершите обновление или восстановление запуска службы.')
        checked=inspect(spec)
        if checked.get('check_sha256')!=expected:raise ValueError('Служба изменилась после проверки. Проверьте её ещё раз.')
        directory=Path(os.environ['ProgramData'])/'OwnDesk/ServiceRemovals'/spec.name/uuid4().hex
        service.secure_directory(directory,admin_only=True)
        record={**checked,'phase':'prepared','started_at':time.time(),'backup_directory':None}
        def checkpoint(phase,**values):
            record.update(phase=phase,**values);save_private(directory/'removal.json',record)
        checkpoint('prepared')
        (directory/'service-before.xml').write_text(spec.xml.read_text(encoding='utf-8'),encoding='utf-8')
        stopped=False
        try:
            if checked['fresh_backup_required']:
                report('Создаём резервную копию перед остановкой службы…')
                destination=Path(os.environ['ProgramData'])/'OwnDesk/Backups'/('BeforeRemoval-'+uuid4().hex)
                service.secure_directory(destination,admin_only=True)
                checkpoint('backing-up',backup_directory=str(destination))
                backup(spec.data,destination,checked['version']);verify(destination)
            if inspect(spec)['check_sha256']!=expected:raise ValueError('Параметры службы изменились до остановки.')
            checkpoint('stopping');report('Останавливаем выбранную службу OwnDesk…')
            update.stop(spec);stopped=True
            after_stop=inspect(spec)
            if (after_stop['host_sha256']!=checked['host_sha256'] or after_stop['xml_sha256']!=checked['xml_sha256']
                    or after_stop['config_fingerprints']!=checked['config_fingerprints']):
                raise RuntimeError('Параметры установки изменились; регистрация не удалялась.')
            checkpoint('unregistering');report('Удаляем регистрацию службы. Файлы программы и данные остаются…')
            try:service.execute([spec.host,'uninstall'],lambda _:None)
            except Exception:
                if registration(spec.name) is not None:raise
            deadline=time.monotonic()+20
            while registration(spec.name) is not None:
                if time.monotonic()>deadline:raise RuntimeError('Windows ещё не удалила службу; проверьте журнал операции.')
                time.sleep(.5)
            if not spec.data.is_dir() or private_fingerprints(spec.data)!=checked['config_fingerprints']:
                raise RuntimeError('Не удалось подтвердить сохранность настроек установки.')
            if update.sha(spec.host)!=checked['host_sha256'] or hashlib.sha256(spec.xml.read_text(encoding='utf-8').encode()).hexdigest()!=checked['xml_sha256']:
                raise RuntimeError('Файлы программы изменились во время удаления регистрации.')
            checkpoint('complete',status='service-unregistered',service_registered=False,data_preserved=True,program_files_preserved=True)
            return {key:record[key] for key in ('status','service','data','app','backup_directory','data_preserved','program_files_preserved')} | {'receipt':str(directory/'removal.json')}
        except Exception:
            restored=False
            if stopped and checked['state']=='Running' and registration(spec.name) is not None:
                try:
                    current=inspect(spec)
                    if (current['xml_sha256']==checked['xml_sha256'] and current['host_sha256']==checked['host_sha256']
                            and current['config_fingerprints']==checked['config_fingerprints']):
                        service.execute([spec.host,'start'],lambda _:None);update.health(spec);restored=True
                except Exception:pass
            checkpoint('failed',previous_running_state_restored=restored)
            raise

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=['check','remove'])
    parser.add_argument('--expected-check',default='')
    parser.add_argument('--confirm-service',default='')
    args=parser.parse_args()
    service.admin_required()
    spec=update.Spec('OwnDeskServer',Path(os.environ['ProgramFiles'])/'OwnDesk/Server/OwnDeskService.exe',
        Path(os.environ['ProgramData'])/'OwnDesk/Server','LocalService')
    result=inspect(spec) if args.action=='check' else remove(spec,args.expected_check,args.confirm_service,
        lambda line:print(json.dumps({'event':'progress','message':line},ensure_ascii=False),flush=True))
    if result['status']=='not-installed':raise RuntimeError('Служба OwnDeskServer не зарегистрирована. Файлы программы и данных не удалялись.')
    print(json.dumps(result,ensure_ascii=False))

if __name__=='__main__':main()
