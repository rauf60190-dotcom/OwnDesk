import importlib.util
from pathlib import Path
import sys

import pytest

INFRA = Path(__file__).resolve().parents[1] / 'infra'
sys.path.insert(0, str(INFRA))
spec = importlib.util.spec_from_file_location('native_restore', INFRA / 'native_restore.py')
restore = importlib.util.module_from_spec(spec)
spec.loader.exec_module(restore)


def test_normal_start_does_not_require_a_restore_or_database_credentials(tmp_path):
    restore.initial_restore(tmp_path, {}, {}, {})
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize('value', [
    {'file': '../backup.dump', 'sha256': 'a' * 64, 'counts': {'sample': 1}},
    {'file': 'initial-restore.dump', 'sha256': 'invalid', 'counts': {'sample': 1}},
    {'file': 'initial-restore.dump', 'sha256': 'a' * 64, 'counts': {'sample': -1}},
    {'file': 'initial-restore.dump', 'sha256': 'a' * 64, 'counts': {'sample': True}},
    {'file': 'initial-restore.dump', 'sha256': 'a' * 64, 'counts': {'sample;DROP': 1}},
    {'file': 'initial-restore.dump', 'sha256': 'a' * 64, 'counts': {}},
])
def test_invalid_restore_is_rejected_before_database_or_filesystem_changes(tmp_path, value):
    with pytest.raises(ValueError):
        restore.initial_restore(tmp_path, {'initial_restore': value}, {}, {})
    assert list(tmp_path.iterdir()) == []
