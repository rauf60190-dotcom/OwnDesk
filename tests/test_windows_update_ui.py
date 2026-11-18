import importlib.util
import json
from pathlib import Path
import sys
from unittest.mock import MagicMock, patch

import pytest

INFRA = Path(__file__).resolve().parents[1] / 'infra'
sys.path.insert(0, str(INFRA))
spec = importlib.util.spec_from_file_location('windows_update_ui', INFRA / 'windows_update_ui.py')
ui = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = ui
spec.loader.exec_module(ui)


def selection(tmp_path):
    archive = tmp_path / 'OwnDesk release.zip'
    archive.write_bytes(b'test archive')
    return ui.Selection('OwnDeskServer', str(archive), 'a' * 64)


def test_confirmation_is_bound_to_service_archive_and_checksum(tmp_path):
    selected = selection(tmp_path)
    state = ui.CheckedUpdate()
    assert not state.matches(selected)
    state.accept(selected, {'status': 'compatible-maintenance-update', 'installed': '0.4.26', 'candidate': '0.4.30'})
    assert state.matches(selected)
    assert not state.matches(ui.Selection('OwnDeskPortal', selected.archive, selected.sha256))
    assert not state.matches(ui.Selection(selected.service, selected.archive + '.other.zip', selected.sha256))
    assert not state.matches(ui.Selection(selected.service, selected.archive, 'b' * 64))
    state.clear()
    assert not state.matches(selected)


def test_command_preserves_paths_as_arguments_without_a_shell(tmp_path):
    selected = selection(tmp_path)
    python = tmp_path / 'Program Files/python.exe'
    args = ui.command(selected, 'check', python=python)
    assert args[0] == str(python)
    assert args[args.index('--archive') + 1] == selected.archive
    assert '--progress' in args


@pytest.mark.parametrize('service,checksum', [('OtherService', 'a' * 64), ('OwnDeskServer', 'short'),
                                           ('OwnDeskServer', 'z' * 64)])
def test_unsupported_selection_does_not_launch_a_command(tmp_path, service, checksum):
    selected = selection(tmp_path)
    with pytest.raises(ValueError):
        ui.command(ui.Selection(service, selected.archive, checksum), 'apply', python=tmp_path / 'python.exe')


def test_recovery_uses_a_transaction_and_does_not_require_an_archive(tmp_path):
    args = ui.command(ui.Selection('OwnDeskPortal'), 'recover', 'a' * 32, python=tmp_path / 'python.exe')
    assert '--archive' not in args and '--sha256' not in args
    assert args[-2:] == ['--transaction', 'a' * 32]
    with pytest.raises(ValueError):
        ui.command(ui.Selection('OwnDeskPortal'), 'recover', '../foreign', python=tmp_path / 'python.exe')


def test_incomplete_check_result_cannot_unlock_apply(tmp_path):
    with pytest.raises(ValueError):
        ui.CheckedUpdate().accept(selection(tmp_path), {'status': 'compatible-maintenance-update'})


def test_progress_protocol_confirms_a_result_instead_of_trusting_exit_code(tmp_path):
    selected = selection(tmp_path)
    child = MagicMock()
    child.__enter__.return_value = child
    child.stdout = [json.dumps({'event': 'progress', 'message': 'Проверяем архив'}) + '\n',
        json.dumps({'status': 'compatible-maintenance-update', 'installed': '0.4.26', 'candidate': '0.4.30'}) + '\n']
    child.wait.return_value = 0
    with patch.object(ui, 'command', return_value=[str(Path(sys.executable)), '-B', 'helper.py']), \
            patch.object(ui.subprocess, 'Popen', return_value=child):
        messages = []
        result = ui.run(selected, 'check', messages.append)
    assert messages == ['Проверяем архив']
    assert result['candidate'] == '0.4.30'


def test_no_result_is_not_reported_as_success(tmp_path):
    child = MagicMock()
    child.__enter__.return_value = child
    child.stdout = []
    child.wait.return_value = 0
    with patch.object(ui, 'command', return_value=[str(Path(sys.executable)), 'helper.py']), \
            patch.object(ui.subprocess, 'Popen', return_value=child), pytest.raises(ui.UpdateError):
        ui.run(selection(tmp_path), 'apply', lambda _: None)


def test_failed_rollback_offers_a_specific_recovery_transaction():
    error = ui.failure('OwnDesk: Rollback could not be verified; use recover --service OwnDeskServer --transaction ' + 'a' * 32)
    assert error.transaction == 'a' * 32
    assert 'Восстановить запуск' in str(error)


def test_preexisting_unfinished_transaction_is_available_for_recovery():
    error = ui.failure('OwnDesk: Recover unfinished transaction first: ' + 'c' * 32)
    assert error.transaction == 'c' * 32
    assert 'Восстановить запуск' in str(error)
