import importlib.util
import json
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

import httpx
import pytest

spec = importlib.util.spec_from_file_location("portable_runtime", Path(__file__).resolve().parents[1] / "infra/native_runtime.py")
runtime = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runtime)


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
