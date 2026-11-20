import importlib.util
import json
from contextlib import ExitStack
import subprocess
from types import SimpleNamespace
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

import httpx
import pytest

spec = importlib.util.spec_from_file_location("portable_runtime", Path(__file__).resolve().parents[1] / "infra/native_runtime.py")
runtime = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runtime)


@pytest.mark.parametrize('failed_probe', [0, 1])
def test_managed_database_does_not_start_or_initialize_when_client_is_blocked(tmp_path, failed_probe):
    results = [SimpleNamespace(returncode=0)] * failed_probe + [SimpleNamespace(returncode=-1058471934)]
    private_env = {'POSTGRES_ADMIN_PASSWORD': 'must-not-be-logged'}
    with ExitStack() as stack, patch.object(runtime.subprocess, 'run', side_effect=results) as run, \
         patch.object(runtime, 'launch') as launch:
        with pytest.raises(RuntimeError, match='Windows Code Integrity') as error:
            runtime.managed_database(stack, tmp_path, {'bin': str(tmp_path / 'bin')}, {}, private_env)
        assert 'must-not-be-logged' not in str(error.value)
        assert '0xC0E90002' in str(error.value)
        launch.assert_not_called()
        assert list(tmp_path.iterdir()) == []
        assert run.call_count == failed_probe + 1


@pytest.mark.parametrize('error', [FileNotFoundError('private-path'), subprocess.TimeoutExpired('private-command', 15)])
def test_postgres_preflight_reports_unavailable_client_without_disclosing_exception(tmp_path, error):
    with patch.object(runtime.subprocess, 'run', side_effect=error):
        with pytest.raises(RuntimeError, match='database was not started') as caught:
            runtime.check_postgres_client(tmp_path, {})
    assert 'private-' not in str(caught.value)


def test_postgres_preflight_loads_actual_client_in_separate_bounded_process(tmp_path):
    with patch.object(runtime.subprocess, 'run', return_value=SimpleNamespace(returncode=0)) as run:
        runtime.check_postgres_client(tmp_path, {'PATH': 'client-path'})
    assert run.call_count == 2
    assert run.call_args_list[0].args[0][-1] == '--version'
    assert run.call_args_list[1].args[0][0] == runtime.sys.executable
    for call in run.call_args_list:
        assert call.kwargs['timeout'] == 15
        assert call.kwargs['stdin'] == subprocess.DEVNULL
        assert call.kwargs['stdout'] == subprocess.DEVNULL
        assert call.kwargs['stderr'] == subprocess.DEVNULL


def test_prepare_preserves_pending_identity_and_never_stores_tunnel_token(tmp_path):
    item = uuid4()
    hostname = "org-" + item.hex + ".owndesk.work"
    keys = []
    fail_once = True
    client_type = httpx.Client

    def respond(request):
        nonlocal fail_once
        assert request.url.host == "owndesk.work"
        if request.url.path.endswith("/pair"):
            data = json.loads(request.content)
            keys.append(data["connector_key"])
            if fail_once:
                fail_once = False
                raise httpx.ReadTimeout("Response lost", request=request)
            return httpx.Response(200, json={"id": str(item)})
        assert request.headers["Authorization"] == "Bearer " + keys[0]
        return httpx.Response(200, json={"hostname": hostname, "token": "private-tunnel-token-never-persist"})

    with patch.object(runtime, "__name__", "portable_runtime"), patch("httpx.Client", side_effect=lambda **kwargs: client_type(
        **kwargs, transport=httpx.MockTransport(respond),
    )):
        with pytest.raises(httpx.ReadTimeout):
            runtime.prepare(tmp_path, "c" * 43, "caddy", "cloudflared", "auto")
        runtime.prepare(tmp_path, "", "caddy", "cloudflared", "auto")
    assert keys[0] == keys[1]
    profile = runtime.profile_for(tmp_path)
    assert profile["public_hostname"] == hostname
    text = "".join(path.read_text() for path in tmp_path.glob("*.json"))
    assert "private-tunnel-token-never-persist" not in text and '"code"' not in text
    first = runtime.get_secrets(tmp_path)
    assert runtime.get_secrets(tmp_path) == first


def test_prepare_cli_accepts_replacement_code_for_an_unpaired_attempt(tmp_path):
    import io
    runtime.save_private(tmp_path / 'portal-connection.json', {'code': 'old-expired-code', 'connector_key': 'same-key'})
    with patch.object(runtime.sys, 'argv', ['native_runtime.py', 'prepare', '--data', str(tmp_path), '--pair-code-stdin']), \
         patch.object(runtime.sys, 'stdin', io.StringIO('fresh-pairing-code\n')), patch.object(runtime, 'prepare') as prepare:
        runtime.main()
    assert prepare.call_args.args[1] == 'fresh-pairing-code'


def test_profile_identity_and_environment_are_isolated(tmp_path, monkeypatch):
    item = uuid4()
    hostname = "org-" + item.hex + ".owndesk.work"
    runtime.save_private(tmp_path / "portal-connection.json", {"id": str(item), "connector_key": "k" * 64})
    profile = {"format": 1, "public_hostname": hostname, "api_port": 8000, "protocol": "http2"}
    runtime.save_private(tmp_path / "runtime-profile.json", profile)
    assert runtime.profile_for(tmp_path) == profile
    monkeypatch.setenv("CLOUDFLARE_API_TOKEN", "portal-only-secret")
    monkeypatch.setenv("DATABASE_URL", "postgresql://user:password@db/organization")
    secret = runtime.get_secrets(tmp_path)
    env = runtime.environment(tmp_path, profile, secret)
    assert "CLOUDFLARE_API_TOKEN" not in env
    assert env["DEBUG"] == "0" and env["OWNDESK_RUNTIME"] == "native"
    assert env["PRIVATE_MEDIA_ROOT"] == str(tmp_path / "private-attachments")
    profile["public_hostname"] = "org-" + uuid4().hex + ".owndesk.work"
    runtime.save_private(tmp_path / "runtime-profile.json", profile)
    with pytest.raises(ValueError, match="does not match"):
        runtime.profile_for(tmp_path)
