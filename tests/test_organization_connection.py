from unittest.mock import patch

import httpx
import pytest

from apps.workspaces.connection import private_state
import json


@pytest.mark.django_db
def test_owner_pairs_from_wizard_and_keys_remain_private(desk, client_for, settings, tmp_path):
    settings.DEPLOYMENT_MODE = "organization"
    settings.PORTAL_CONNECTOR_STATE_FILE = str(tmp_path / "connector.json")
    owner = desk["admin"]
    owner.is_owner = True
    owner.save(update_fields=["is_owner"])
    client = client_for(owner)
    url = "/api/v1/workspace/portal-connection"
    assert client.get(url).json() == {"available": True, "paired": False, "public_access": False}
    calls = []

    def response(request):
        calls.append(request.url.path)
        assert request.url.host == "owndesk.work"
        if request.url.path.endswith("/pair"):
            return httpx.Response(200, json={"id": str(desk["ws"].pk), "name": "Organization"})
        return httpx.Response(200, json={"ok": True})

    with patch("apps.workspaces.management.commands.connect_portal.httpx.Client",
               return_value=httpx.Client(transport=httpx.MockTransport(response))):
        assert client.post(url, {"code": "c" * 43}, format="json").json() == {"paired": True, "public_access": False}
    state = private_state(tmp_path / "connector.json")
    assert state["id"] == str(desk["ws"].pk)
    assert "code" not in state
    assert len(state["connector_key"]) >= 43
    assert "connector_key" not in client.get(url).json()
    assert len(calls) == 2
    assert client_for(desk["operator"]).get(url).status_code == 403
    assert client_for(desk["operator"]).post(url, {"code": "c" * 43}, format="json").status_code == 403


@pytest.mark.django_db
def test_network_failure_keeps_pending_key_for_retry(desk, client_for, settings, tmp_path):
    settings.DEPLOYMENT_MODE = "organization"
    target = tmp_path / "connector.json"
    settings.PORTAL_CONNECTOR_STATE_FILE = str(target)
    owner = desk["admin"]
    owner.is_owner = True
    owner.save(update_fields=["is_owner"])
    client = client_for(owner)

    def offline(request):
        raise httpx.ConnectError("offline", request=request)

    with patch("apps.workspaces.management.commands.connect_portal.httpx.Client",
               return_value=httpx.Client(transport=httpx.MockTransport(offline))):
        result = client.post("/api/v1/workspace/portal-connection", {"code": "c" * 43}, format="json")
    assert result.status_code == 400
    state = private_state(target)
    assert state["code"] == "c" * 43
    assert state["connector_key"]
    assert state["connector_key"] not in str(result.json())
    settings.DEPLOYMENT_MODE = "portal"
    assert client.get("/api/v1/workspace/portal-connection").status_code == 404


@pytest.mark.django_db
def test_owner_prepares_address_from_wizard_without_exposing_tunnel_token(desk, client_for, settings, tmp_path):
    settings.DEPLOYMENT_MODE = "organization"
    target = tmp_path / "connector.json"
    settings.PORTAL_CONNECTOR_STATE_FILE = str(target)
    key = "k" * 64
    target.write_text(json.dumps({"id": str(desk["ws"].pk), "connector_key": key}))
    owner = desk["admin"]
    owner.is_owner = True
    owner.save()
    client = client_for(owner)
    hostname = "org-" + desk["ws"].pk.hex + ".owndesk.work"

    def response(request):
        assert request.headers["Authorization"] == "Bearer " + key
        if request.url.path.endswith("/configuration"):
            return httpx.Response(200, json={"hostname": hostname, "token": "private-tunnel-token"})
        return httpx.Response(200, json={"ok": True})

    with patch("apps.workspaces.management.commands.connect_portal.httpx.Client",
               return_value=httpx.Client(transport=httpx.MockTransport(response))):
        result = client.post("/api/v1/workspace/portal-connection", {"prepare_address": True}, format="json")
        assert result.status_code == 200
    saved = private_state(target)
    assert saved["public_hostname"] == hostname
    assert "private-tunnel-token" not in target.read_text()
    result = client.get("/api/v1/workspace/portal-connection").json()
    assert result["prepared_hostname"] == hostname and result["public_access"] is False
    assert key not in json.dumps(result)
