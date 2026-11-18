import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

spec = importlib.util.spec_from_file_location('linux_setup', Path(__file__).resolve().parents[1] / 'infra/linux_setup.py')
linux = importlib.util.module_from_spec(spec)
spec.loader.exec_module(linux)
ORIGIN = 'https://org-' + 'a' * 32 + '.owndesk.work'
LINK = ORIGIN + '/#setup=' + 's' * 43


def response(endpoint=ORIGIN + '/api/v1/health/live', body=b'{"status":"alive"}', status=200):
    value = MagicMock()
    value.__enter__.return_value = value
    value.status = status
    value.geturl.return_value = endpoint
    value.read.return_value = body
    return value


def test_external_check_never_transmits_setup_secret_and_uses_installer_identity():
    with patch.object(linux, 'build_opener') as opener:
        opener.return_value.open.return_value = response()
        assert linux.public_ready(LINK) == LINK
    request = opener.return_value.open.call_args.args[0]
    assert request.full_url == ORIGIN + '/api/v1/health/live'
    assert 's' * 43 not in request.full_url
    assert request.get_header('User-agent').startswith('OwnDesk/')
    assert isinstance(opener.call_args.args[0], linux.NoRedirect)


@pytest.mark.parametrize('link', [
    'http://' + ORIGIN[8:] + '/', ORIGIN + ':8443/', ORIGIN + '.other.example/',
    ORIGIN + '/?setup=secret', ORIGIN + '/#other=value', ORIGIN + '/#setup=short',
    ORIGIN + '/#setup=' + 'a' * 32 + '&setup=' + 'b' * 32,
    'https://user:pass@' + ORIGIN[8:] + '/',
])
def test_unexpected_link_is_rejected_before_network_access(link):
    with patch.object(linux, 'build_opener') as opener, pytest.raises(ValueError):
        linux.public_ready(link)
    opener.assert_not_called()


@pytest.mark.parametrize('value', [response(status=503), response(body=b'{"status":"starting"}'),
    response(endpoint='https://another.example/api/v1/health/live'), response(body=b'a' * 4097)])
def test_container_status_does_not_replace_public_readiness(value):
    with patch.object(linux, 'build_opener') as opener:
        opener.return_value.open.return_value = value
        with pytest.raises((OSError, ValueError)):
            linux.public_ready(LINK)


@pytest.mark.parametrize('action', ['start', 'stop', 'status', 'setup-link'])
def test_management_without_installation_does_not_create_state_or_secrets(tmp_path, action):
    with patch.object(linux, 'ROOT', tmp_path), patch('sys.argv', ['setup', action]), \
            patch.object(linux.subprocess, 'run') as command, pytest.raises(RuntimeError, match='Run install first'):
        linux.main()
    command.assert_not_called()
    assert not (tmp_path / '.runtime').exists()


def test_install_keeps_existing_credentials_and_pairs_through_stdin(tmp_path):
    state = tmp_path / '.runtime/linux'
    state.mkdir(parents=True)
    original = {'project': 'owndesk-' + 'a' * 12, 'db_admin_password': 'b' * 43, 'db_app_password': 'c' * 43}
    (state / 'installation.json').write_text(json.dumps(original))
    (tmp_path / 'VERSION').write_text('0.4.27')
    commands = []
    def run(args, **kwargs):
        commands.append((args, kwargs))
        return SimpleNamespace(stdout=LINK + '\n', returncode=0)
    with patch.object(linux, 'ROOT', tmp_path), patch('sys.argv', ['setup', 'install', '--protocol', 'http2']), \
            patch.object(linux.subprocess, 'run', side_effect=run), \
            patch.object(linux.getpass, 'getpass', return_value='pair-secret'), patch.object(linux, 'public_ready') as ready:
        linux.main()
    assert json.loads((state / 'installation.json').read_text()) == original
    ready.assert_called_once_with(LINK)
    pairing = [(args, kw) for args, kw in commands if '--pair-code-stdin' in args]
    assert len(pairing) == 1 and pairing[0][1]['input'] == 'pair-secret\n'
    assert all('pair-secret' not in ' '.join(args) and original['db_admin_password'] not in ' '.join(args) for args, _ in commands)
    assert any('--wait' in args and args[-1] == 'db' for args, _ in commands)


def test_private_link_is_not_printed_when_public_api_is_unavailable(tmp_path, capsys):
    state = tmp_path / '.runtime/linux'
    state.mkdir(parents=True)
    (state / 'installation.json').write_text(json.dumps({'project': 'owndesk-' + 'a' * 12,
        'db_admin_password': 'b' * 43, 'db_app_password': 'c' * 43}))
    (tmp_path / 'VERSION').write_text('0.4.27')
    with patch.object(linux, 'ROOT', tmp_path), patch('sys.argv', ['setup', 'setup-link']), \
            patch.object(linux.subprocess, 'run', return_value=SimpleNamespace(stdout=LINK)), \
            patch.object(linux, 'public_ready', side_effect=OSError('offline')), \
            patch.object(linux.time, 'monotonic', side_effect=[0, 1, 181]), patch.object(linux.time, 'sleep'), \
            pytest.raises(RuntimeError, match='did not become ready'):
        linux.main()
    assert LINK not in capsys.readouterr().out
