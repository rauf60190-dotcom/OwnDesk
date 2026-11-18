import importlib.util
import json
from pathlib import Path
import subprocess
import sys
from unittest.mock import patch
from zipfile import ZipFile

import pytest

TOOLS = Path(__file__).resolve().parents[1] / 'tools'

def load(name):
    spec = importlib.util.spec_from_file_location(name, TOOLS / (name + '.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

with patch.dict(sys.modules, {'prepare_source_review': load('prepare_source_review')}):
    candidate = load('package_candidate')


@pytest.fixture
def repository(tmp_path, monkeypatch):
    root = tmp_path / 'source'
    root.mkdir()
    def git(*args):
        return subprocess.check_output(['git', '-C', str(root), *args], stderr=subprocess.PIPE)
    git('init', '--quiet')
    for name, text in {'VERSION': '0.4.43', 'frontend/pnpm-lock.yaml': 'lock',
                       'backend/requirements.txt': 'requirements', 'app.py': 'source'}.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    git('add', '.')
    git('-c', 'user.name=CI fixture', '-c', 'user.email=fixture@example.invalid',
        'commit', '--quiet', '-m', 'fixture')
    dist = root / 'frontend/dist'
    dist.mkdir()
    (dist / 'index.html').write_text('built interface')
    (root / 'secrets.json').write_text('not tracked, must not ship')
    monkeypatch.setattr(candidate, 'ROOT', root)
    return root, git


def test_only_tracked_source_and_frontend_build_are_packaged(repository, tmp_path):
    root, git = repository
    output = tmp_path / 'candidate'
    candidate.package(output)
    metadata = json.loads((output / 'release.json').read_text())
    assert metadata['source_commit'] == git('rev-parse', 'HEAD').decode().strip()
    assert metadata['candidate_only'] and not metadata['public_release_approved']
    with ZipFile(output / 'OwnDesk.zip') as archive:
        assert 'OwnDesk/frontend/dist/index.html' in archive.namelist()
        assert 'OwnDesk/secrets.json' not in archive.namelist()
        assert len(archive.namelist()) == metadata['files']
    with pytest.raises(ValueError, match='fresh'):
        candidate.package(output)


def test_dirty_or_private_tracked_source_is_rejected(repository, tmp_path):
    root, git = repository
    (root / 'app.py').write_text('changed')
    with pytest.raises(ValueError, match='committed'):
        candidate.package(tmp_path / 'dirty')
    git('checkout', '--', 'app.py')
    git('add', 'secrets.json')
    git('-c', 'user.name=CI fixture', '-c', 'user.email=fixture@example.invalid',
        'commit', '--quiet', '-m', 'invalid fixture')
    with pytest.raises(ValueError, match='Unexpected tracked entry'):
        candidate.package(tmp_path / 'private')
