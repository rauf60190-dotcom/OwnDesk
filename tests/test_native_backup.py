import importlib.util
import json
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location('native_backup', Path(__file__).resolve().parents[1] / 'infra/native_backup.py')
backup = importlib.util.module_from_spec(spec)
spec.loader.exec_module(backup)


@pytest.mark.parametrize('relative', ['../secret', '/absolute', 'folder/../secret', 'folder\\secret',
    'C:/secret', 'file:stream', './file', 'folder//file', ''])
def test_backup_paths_cannot_escape_private_directory(tmp_path, relative):
    with pytest.raises(ValueError):
        backup.safe_file(tmp_path, relative)


def make_backup(directory):
    files = {}
    for name in ('database.dump', 'config/secrets.json', 'config/runtime-profile.json'):
        path = directory / name
        path.parent.mkdir(exist_ok=True)
        path.write_bytes(b'test-only')
        files[name] = backup.fingerprint(path)
    manifest = {'format': 1, 'kind': 'organization', 'version': '0.4.28', 'ready_attachments': 0, 'files': files}
    (directory / 'manifest.json').write_text(json.dumps(manifest))
    return manifest


def test_verify_rejects_corruption_before_any_restore(tmp_path):
    make_backup(tmp_path)
    assert backup.verify(tmp_path)['kind'] == 'organization'
    (tmp_path / 'database.dump').write_bytes(b'changed')
    with pytest.raises(ValueError, match='checksum'):
        backup.verify(tmp_path)


def test_verify_requires_a_completed_manifest(tmp_path):
    (tmp_path / 'database.dump').write_bytes(b'partial')
    with pytest.raises(ValueError):
        backup.verify(tmp_path)


def test_manifest_cannot_reference_foreign_files(tmp_path):
    manifest = make_backup(tmp_path)
    manifest['files']['../foreign'] = manifest['files']['database.dump']
    (tmp_path / 'manifest.json').write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match='path'):
        backup.verify(tmp_path)


def test_existing_destination_is_never_overwritten(tmp_path):
    (tmp_path / 'keep').write_text('original')
    with pytest.raises(ValueError, match='empty'):
        backup.backup(tmp_path / 'source', tmp_path, '0.4.28')
    assert (tmp_path / 'keep').read_text() == 'original'
