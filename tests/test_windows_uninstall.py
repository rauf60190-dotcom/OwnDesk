import hashlib
import json
from pathlib import Path
import sys
from unittest.mock import Mock

import pytest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'infra'))
import windows_uninstall as removal
import windows_uninstall_ui as ui
import native_backup

@pytest.fixture
def installed(tmp_path,monkeypatch):
    app=tmp_path/'Program Files/OwnDesk/Server';data=tmp_path/'ProgramData/OwnDesk/Server'
    app.mkdir(parents=True);data.mkdir(parents=True)
    host=app/'OwnDeskService.exe';host.write_bytes(b'synthetic-test-wrapper')
    (app/'VERSION').write_text('0.4.43')
    for name in removal.REQUIRED_CONFIGS:(data/name).write_text('{}')
    (data/'database-sentinel').write_bytes(b'database must stay')
    (data/'private-attachments').mkdir();(data/'private-attachments/file.jpg').write_bytes(b'attachment must stay')
    spec=removal.update.Spec('OwnDeskServer',host,data,'LocalService')
    spec.xml.write_text(removal.service.service_xml(app,data,app/'runtime/venv/Scripts/python.exe'))
    monkeypatch.setenv('ProgramData',str(tmp_path/'ProgramData'))
    monkeypatch.setattr(removal,'TRUSTED_WRAPPER_SHA',hashlib.sha256(host.read_bytes()).hexdigest())
    monkeypatch.setattr(removal.service,'admin_required',lambda:None)
    monkeypatch.setattr(removal.service,'secure_directory',lambda target,**kwargs:target.mkdir(parents=True,exist_ok=True))
    state={'present':True,'State':'Running'};calls=[]
    monkeypatch.setattr(removal,'registration',lambda name:dict(state) if state['present'] else None)
    monkeypatch.setattr(removal.update,'query',lambda selected:dict(state))
    monkeypatch.setattr(native_backup,'backup',Mock())
    monkeypatch.setattr(native_backup,'verify',Mock(return_value={}))
    def execute(args,report):
        calls.append(args[1])
        if args[1]=='stopwait':state['State']='Stopped'
        elif args[1]=='uninstall':state['present']=False
        elif args[1]=='start':state['State']='Running'
    monkeypatch.setattr(removal.service,'execute',execute)
    monkeypatch.setattr(removal.update,'health',lambda selected:None)
    return spec,state,calls

def snapshot(spec):
    return {str(path):path.read_bytes() for root in (spec.host.parent,spec.data) for path in root.rglob('*') if path.is_file()}

def test_running_service_is_backed_up_and_only_registration_removed(installed):
    spec,state,calls=installed;before=snapshot(spec)
    checked=removal.inspect(spec)
    assert not calls
    result=removal.remove(spec,checked['check_sha256'],spec.name,lambda _:None)
    assert result['status']=='service-unregistered' and result['data_preserved'] and result['program_files_preserved']
    assert calls==['stopwait','uninstall'] and not state['present']
    native_backup.backup.assert_called_once();native_backup.verify.assert_called_once()
    assert snapshot(spec)==before

def test_stopped_service_keeps_all_files_without_starting_database(installed):
    spec,state,calls=installed;state['State']='Stopped';before=snapshot(spec)
    result=removal.remove(spec,removal.inspect(spec)['check_sha256'],spec.name,lambda _:None)
    assert result['backup_directory'] is None and calls==['uninstall']
    native_backup.backup.assert_not_called();assert snapshot(spec)==before

def test_backup_failure_does_not_stop_or_unregister(installed):
    spec,state,calls=installed;native_backup.backup.side_effect=RuntimeError('backup unavailable')
    with pytest.raises(RuntimeError,match='backup unavailable'):
        removal.remove(spec,removal.inspect(spec)['check_sha256'],spec.name,lambda _:None)
    assert not calls and state['present'] and state['State']=='Running'

@pytest.mark.parametrize('change',['xml','state','configuration','wrapper'])
def test_stale_check_cannot_remove_a_changed_installation(installed,change):
    spec,state,calls=installed;checked=removal.inspect(spec)['check_sha256']
    if change=='xml':spec.xml.write_text(spec.xml.read_text()+'\n')
    if change=='state':state['State']='Stopped'
    if change=='configuration':(spec.data/'secrets.json').write_text('{"changed":true}')
    if change=='wrapper':spec.host.write_bytes(b'other executable')
    with pytest.raises((ValueError,RuntimeError)):
        removal.remove(spec,checked,spec.name,lambda _:None)
    assert not calls and state['present']

def test_pending_update_blocks_removal(installed):
    spec,state,calls=installed
    journal=Path(removal.os.environ['ProgramData'])/'OwnDesk/ServiceUpdates'/spec.name/'transaction/update.json'
    journal.parent.mkdir(parents=True);journal.write_text(json.dumps({'phase':'recovery-required'}))
    with pytest.raises(RuntimeError,match='обновление'):
        removal.remove(spec,removal.inspect(spec)['check_sha256'],spec.name,lambda _:None)
    assert not calls

def test_uninstall_failure_restarts_only_the_original_running_service(installed,monkeypatch):
    spec,state,calls=installed;original=removal.service.execute
    def fail(args,report):
        if args[1]=='uninstall':raise RuntimeError('SCM refused')
        return original(args,report)
    monkeypatch.setattr(removal.service,'execute',fail)
    with pytest.raises(RuntimeError,match='SCM refused'):
        removal.remove(spec,removal.inspect(spec)['check_sha256'],spec.name,lambda _:None)
    assert state['present'] and state['State']=='Running' and calls==['stopwait','start']

def test_bad_confirmation_and_portal_target_are_rejected(installed):
    spec,state,calls=installed;token=removal.inspect(spec)['check_sha256']
    with pytest.raises(ValueError):removal.remove(spec,token,'OwnDeskPortal')
    portal=removal.update.Spec('OwnDeskPortal',spec.host,spec.data,'NetworkService')
    with pytest.raises(ValueError):removal.remove(portal,token,'OwnDeskPortal')
    assert not calls

def test_absent_service_is_read_only(installed):
    spec,state,calls=installed;state['present']=False
    assert removal.inspect(spec)['status']=='not-installed' and not calls

def test_ui_will_not_run_removal_without_exact_confirmation(monkeypatch):
    runner=Mock();monkeypatch.setattr(ui,'run_process',runner)
    with pytest.raises(ValueError):ui.run('remove',lambda _:None,'f'*64,'OwnDeskPortal')
    runner.assert_not_called()
