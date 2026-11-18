import hashlib
import importlib.util
from pathlib import Path
import sys
from unittest.mock import patch
from unittest.mock import MagicMock
from types import SimpleNamespace
import json
import zipfile

import pytest

INFRA = Path(__file__).resolve().parents[1] / 'infra'
sys.path.insert(0, str(INFRA))
spec = importlib.util.spec_from_file_location('linux_update', INFRA/'linux_update.py')
update = importlib.util.module_from_spec(spec)
spec.loader.exec_module(update)


def release(tmp_path, *, change=None, extra=None):
    root=tmp_path/'installed'
    root.mkdir()
    items={'VERSION':b'0.4.35', 'backend/manage.py':b'manage', 'backend/requirements.txt':b'pinned',
        'frontend/dist/index.html':b'frontend', 'infra/native_runtime.py':b'run', 'infra/native.Dockerfile':b'FROM pinned',
        'infra/linux_setup.py':b'setup','infra/linux_backup.py':b'backup','infra/linux_backup_worker.py':b'worker',
        'infra/linux_backup_schedule.py':b'schedule', 'infra/linux_update.py':b'updater',
        'OwnDesk-Update.sh':b'wrapper', 'compose.native.yaml':b'compose', '.dockerignore':b'ignore'}
    for name,value in items.items():
        path=root/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(value)
    (root/'VERSION').write_text('0.4.34')
    if change:items[change]=b'changed application'
    if extra:items[extra]=b'payload'
    archive=tmp_path/'release.zip'
    with zipfile.ZipFile(archive,'w') as package:
        for name,value in items.items():
            entry=zipfile.ZipInfo('placeholder')
            entry.filename=entry.orig_filename='OwnDesk/'+name
            package.writestr(entry,value)
    return root,archive,hashlib.sha256(archive.read_bytes()).hexdigest()


def test_maintenance_update_allows_only_tool_and_version_changes(tmp_path):
    root,archive,digest=release(tmp_path)
    candidate=update.archive_manifest(archive,digest)
    assert update.compatibility(root,candidate)=='0.4.34'


@pytest.mark.parametrize('name',['backend/manage.py','backend/requirements.txt','frontend/dist/index.html',
    'infra/native_runtime.py','infra/linux_backup.py','infra/native.Dockerfile','compose.native.yaml','.dockerignore'])
def test_application_runtime_or_dependency_changes_rejected(tmp_path,name):
    root,archive,digest=release(tmp_path,change=name)
    with pytest.raises(ValueError,match='Maintenance update refused'):
        update.compatibility(root,update.archive_manifest(archive,digest))


@pytest.mark.parametrize('name',['../outside','/absolute','infra/../../outside','infra\\wrong','data:stream','.runtime/secrets.json'])
def test_unsafe_archive_rejected_before_docker(tmp_path,name):
    _,archive,digest=release(tmp_path,extra=name)
    with pytest.raises(ValueError):update.archive_manifest(archive,digest)


def test_unknown_infra_module_cannot_shadow_python_standard_library(tmp_path):
    root,archive,digest=release(tmp_path,extra='infra/json.py')
    with pytest.raises(ValueError,match='Maintenance update refused'):
        update.compatibility(root,update.archive_manifest(archive,digest))


def test_bad_digest_rejected_before_opening_zip(tmp_path):
    _,archive,_=release(tmp_path)
    with patch.object(update,'ZipFile') as opener,pytest.raises(ValueError,match='SHA-256'):
        update.archive_manifest(archive,'0'*64)
    opener.assert_not_called()


def test_duplicate_case_and_symlink_archive_members_rejected(tmp_path):
    _,archive,_=release(tmp_path)
    with zipfile.ZipFile(archive,'a') as package:package.writestr('OwnDesk/BACKEND/manage.py',b'duplicate')
    with pytest.raises(ValueError,match='duplicate'):
        update.archive_manifest(archive,hashlib.sha256(archive.read_bytes()).hexdigest())


def test_recovery_cannot_choose_path_outside_update_directory(tmp_path):
    instance=object.__new__(update.Updater);instance.state=tmp_path
    with pytest.raises(ValueError,match='identity'):instance.path('../escape')


def test_pending_backup_prevents_update(tmp_path):
    instance=object.__new__(update.Updater)
    instance.pending=tmp_path/'pending';instance.installation=type('Installation',(),{'marker':tmp_path/'backup-marker'})()
    instance.installation.marker.touch()
    with pytest.raises(RuntimeError,match='Pending maintenance'):instance.guard()


@pytest.mark.parametrize('phase',['marker-without-pointer','rollback-without-marker','committed-with-pointer'])
def test_recovery_handles_journal_write_boundaries(tmp_path,phase):
    instance=object.__new__(update.Updater)
    instance.state=tmp_path;instance.pending=tmp_path/'update-pending.json';instance.last=tmp_path/'update-last.json'
    project='owndesk-'+'a'*12;identity='b'*24
    instance.installation=SimpleNamespace(project=project,marker=tmp_path/'backup-recovery.json')
    folder=tmp_path/'updates'/identity;folder.mkdir(parents=True,mode=0o700)
    record={'format':1,'id':identity,'project':project,'state':'prepared'}
    if phase=='rollback-without-marker':
        record['state']='rolling-back';update.atomic(instance.last,{'id':identity})
    else:
        update.atomic(instance.installation.marker,{'project':project,'kind':'linux-update','id':identity})
    if phase=='committed-with-pointer':
        record['state']='committed';update.atomic(instance.pending,{'id':identity})
    update.atomic(folder/'transition.json',record)
    instance.switch=MagicMock();instance.validate=MagicMock()
    result=instance.recover()
    assert result['status']=='recovered' and result['database_restored'] is False
    assert not instance.pending.exists() and not instance.installation.marker.exists()
    if phase=='committed-with-pointer':
        instance.switch.assert_not_called()
        assert instance.validate.call_args.args[1]=='new'
    else:assert instance.switch.call_args.args[1]=='old'
