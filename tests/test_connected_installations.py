from datetime import timedelta
import json
from unittest.mock import patch

import httpx
import pytest
from django.core.management import call_command
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts.registration import digest
from apps.workspaces.models import ConnectedInstallation


@pytest.mark.django_db
def test_pairing_retry_owner_isolation_heartbeat_and_revocation(desk, client_for, settings):
    settings.DEPLOYMENT_MODE = "portal"
    owner = client_for(desk["admin"])
    other = client_for(desk["second"])
    root = "/api/v1/portal/installations"
    response = owner.post(root, {"name": "Main office"}, format="json")
    assert response.status_code == 201
    item = response.json()
    code = item["pairing_code"]
    assert ConnectedInstallation.objects.get(pk=item["id"]).code_hash == digest(code)
    assert other.get(root).json()["results"] == []
    assert other.post(f"{root}/{item['id']}/revoke").status_code == 404
    listed = owner.get(root).json()["results"][0]
    assert "pairing_code" not in listed and "code_hash" not in listed
    guest = APIClient()
    key = "test-connector-key-" + "x" * 48
    payload = {"code": code, "connector_key": key, "version": "0.4.13"}
    first = guest.post(root + "/pair", payload, format="json")
    assert first.status_code == 200
    assert guest.post(root + "/pair", payload, format="json").json() == first.json()
    attacker = {**payload, "connector_key": "z" * 64}
    assert guest.post(root + "/pair", attacker, format="json").status_code == 400
    heartbeat = f"{root}/{item['id']}/heartbeat"
    assert guest.post(heartbeat).status_code == 403
    assert guest.post(heartbeat, HTTP_AUTHORIZATION="Bearer " + key).status_code == 200
    assert owner.get(root).json()["results"][0]["connection_status"] == "connected"
    ConnectedInstallation.objects.filter(pk=item["id"]).update(last_seen_at=timezone.now() - timedelta(minutes=3))
    assert owner.get(root).json()["results"][0]["connection_status"] == "offline"
    assert owner.post(f"{root}/{item['id']}/revoke").status_code == 200
    assert guest.post(heartbeat, HTTP_AUTHORIZATION="Bearer " + key).status_code == 403
    assert guest.post(root + "/pair", payload, format="json").status_code == 400


@pytest.mark.django_db
def test_expired_pairing_and_modes(desk, client_for, settings):
    root = "/api/v1/portal/installations"
    owner = client_for(desk["admin"])
    settings.DEPLOYMENT_MODE = "organization"
    assert owner.get(root).status_code == 404
    settings.DEPLOYMENT_MODE = "portal"
    item = owner.post(root, {"name": "Expired"}, format="json").json()
    ConnectedInstallation.objects.filter(pk=item["id"]).update(code_expires_at=timezone.now() - timedelta(seconds=1))
    assert APIClient().post(root + "/pair", {
        "code": item["pairing_code"], "connector_key": "x" * 64, "version": "0.4.13",
    }, format="json").status_code == 400
    assert owner.get(root + "/unexpected").status_code == 404
    assert owner.get("/api/v1/tickets").status_code == 404


@pytest.mark.django_db
def test_connector_can_retry_pairing_after_lost_response(desk, settings, tmp_path):
    settings.DEPLOYMENT_MODE = "organization"
    target = tmp_path / "private" / "connector.json"
    target.parent.mkdir()
    state = {"code": "c" * 43, "connector_key": "k" * 64}
    target.write_text(json.dumps(state), encoding="utf-8")
    paths = []

    def response(request):
        paths.append(request.url.path)
        if request.url.path.endswith("/pair"):
            payload = json.loads(request.content)
            assert payload["connector_key"] == state["connector_key"]
            return httpx.Response(200, json={"id": str(desk["ws"].pk), "name": "Main"})
        assert request.headers["Authorization"] == "Bearer " + state["connector_key"]
        return httpx.Response(200, json={"ok": True})

    client = httpx.Client(transport=httpx.MockTransport(response))
    with patch("apps.workspaces.management.commands.connect_portal.httpx.Client", return_value=client):
        call_command("connect_portal", state=str(target), once=True)
    saved = json.loads(target.read_text())
    assert saved == {"id": str(desk["ws"].pk), "connector_key": state["connector_key"]}
    assert paths == [
        "/api/v1/portal/installations/pair",
        f"/api/v1/portal/installations/{desk['ws'].pk}/heartbeat",
    ]
