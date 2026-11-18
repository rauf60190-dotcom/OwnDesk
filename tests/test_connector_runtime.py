import json
import threading
from unittest.mock import MagicMock, patch
from uuid import uuid4

import httpx
import pytest
from django.core.management.base import CommandError

from apps.workspaces.connector_access import signature
from apps.workspaces.connector_runtime import ConnectorRuntime, Revoked


def test_supervisor_starts_restarts_and_stops_dedicated_process(tmp_path):
    item_id = str(uuid4())
    key = "k" * 64
    token = "dedicated-" + "x" * 64
    hostname = "org-" + item_id.replace("-", "") + ".owndesk.work"
    target = tmp_path / "connector.json"
    target.write_text(json.dumps({"id": item_id, "connector_key": key}))
    runtime = ConnectorRuntime(target, "cloudflared.exe")
    revoked = False
    origin_ready = True

    def respond(request):
        if request.url.host == "127.0.0.1":
            assert "authorization" not in request.headers
            return httpx.Response(200, json={"proof": signature(
                key, item_id, hostname, request.url.params["challenge"], origin_ready,
            )})
        assert request.headers["Authorization"] == "Bearer " + key
        if revoked:
            return httpx.Response(403)
        if request.url.path.endswith("/configuration"):
            result = {"hostname": hostname, "token": token}
        elif request.url.path.endswith("/activate"):
            result = {"public_access": True, "public_url": "https://" + hostname}
        else:
            result = {"ok": True}
        return httpx.Response(200, json=result)

    process = MagicMock()
    process.poll.return_value = None
    replacement = MagicMock()
    replacement.poll.return_value = None
    client = httpx.Client(transport=httpx.MockTransport(respond))
    with patch("apps.workspaces.connector_runtime.public_ready", return_value=True), patch(
        "apps.workspaces.connector_runtime.subprocess.Popen", side_effect=[process, replacement],
    ) as spawn:
        runtime.cycle(client)
        args, kwargs = spawn.call_args
        assert token not in str(args) and key not in str(args)
        assert kwargs["env"]["TUNNEL_TOKEN"] == token
        status = json.loads(runtime.status_path.read_text())
        assert status["state"] == "active"
        assert token not in json.dumps(status) and key not in json.dumps(status)
        runtime.cycle(client)
        assert spawn.call_count == 1
        process.poll.return_value = 1
        runtime.cycle(client)
        assert spawn.call_count == 2
        origin_ready = False
        with pytest.raises(CommandError, match="secure readiness"):
            runtime.cycle(client)
        revoked = True
        revoked_client = httpx.Client(transport=httpx.MockTransport(respond))
        with patch("apps.workspaces.connector_runtime.httpx.Client", return_value=revoked_client):
            runtime.run(threading.Event())
        process.terminate.assert_not_called()
        replacement.terminate.assert_called_once()
        assert runtime.process is None
        assert json.loads(runtime.status_path.read_text())["state"] == "revoked"


def test_network_failure_stops_process_and_parent_stop_is_recorded(tmp_path):
    item_id = str(uuid4())
    target = tmp_path / "connector.json"
    target.write_text(json.dumps({"id": item_id, "connector_key": "k" * 64}))
    runtime = ConnectorRuntime(target, "cloudflared.exe")
    process = MagicMock()
    process.poll.return_value = None
    runtime.process = process
    stop = threading.Event()

    def offline(request):
        stop.set()
        raise httpx.ConnectError("secret must not reach status", request=request)

    client = httpx.Client(transport=httpx.MockTransport(offline))
    with patch("apps.workspaces.connector_runtime.httpx.Client", return_value=client):
        runtime.run(stop)
    process.terminate.assert_called_once()
    status = json.loads(runtime.status_path.read_text())
    assert status["state"] == "stopped" and "secret" not in json.dumps(status)
