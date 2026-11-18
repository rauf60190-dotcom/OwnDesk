import json
from datetime import timedelta
from unittest.mock import patch
from uuid import uuid4

import httpx
import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from django.utils import timezone

from apps.workspaces.models import ConnectedInstallation, InstallationRoute
from apps.workspaces.tunnels import prepare_route, TunnelError
from apps.accounts.registration import digest
from rest_framework.test import APIClient


@pytest.fixture
def cloud(settings):
    settings.DEPLOYMENT_MODE = "portal"
    settings.CLOUDFLARE_API_TOKEN = "private-account-token"
    settings.CLOUDFLARE_ACCOUNT_ID = "a" * 32
    settings.CLOUDFLARE_ZONE_ID = "b" * 32
    settings.INSTALLATIONS_DOMAIN = "owndesk.work"
    state = {"tunnels": [], "records": [], "calls": [], "fail_dns": False, "fail_block": False}

    def respond(request):
        assert request.headers["authorization"] == "Bearer private-account-token"
        path = request.url.path
        state["calls"].append((request.method, path))
        data = json.loads(request.content) if request.content else {}
        if path.endswith("/token"):
            result = "dedicated-token-" + "x" * 64
        elif path.endswith("/configurations"):
            rules = data["config"]["ingress"]
            if len(rules) == 1:
                assert rules == [{"service": "http_status:404"}]
            else:
                assert len(rules) == 2 and rules[1] == {"service": "http_status:404"}
                assert rules[0]["service"] == "http://127.0.0.1:5173"
                assert rules[0]["path"] == "^/api/v1/connector/proof$"
            if state["fail_block"]:
                return httpx.Response(503, json={"secret": "never-display-provider-response"})
            result = {}
        elif path.endswith("/cfd_tunnel"):
            if request.method == "GET":
                result = state["tunnels"]
            else:
                result = {"id": str(uuid4()), "name": data["name"], "remote_config": True,
                          "token": "private-tunnel-token"}
                state["tunnels"].append(result)
        elif path.endswith("/dns_records"):
            if request.method == "GET":
                result = state["records"]
            else:
                result = {"id": "d" * 32, **data}
                state["records"].append(result)
                if state["fail_dns"]:
                    state["fail_dns"] = False
                    raise httpx.ReadTimeout("lost DNS response with private material", request=request)
        elif request.method == "DELETE":
            state["records"].clear()
            result = {}
        else:
            raise AssertionError((request.method, path))
        return httpx.Response(200, json={"success": True, "result": result})

    original_client = httpx.Client
    with patch("apps.workspaces.tunnels.httpx.Client", side_effect=lambda **kwargs: original_client(
        **kwargs, transport=httpx.MockTransport(respond),
    )):
        yield state


def installation(desk):
    return ConnectedInstallation.objects.create(
        owner=desk["admin"].user, name="Company", code_hash=uuid4().hex,
        code_expires_at=timezone.now() + timedelta(minutes=15), paired_at=timezone.now(),
    )


@pytest.mark.django_db
def test_prepare_retry_and_no_secrets_in_status(desk, client_for, cloud):
    item = installation(desk)
    first = prepare_route(item.pk)
    second = prepare_route(item.pk)
    assert first.pk == second.pk and second.state == "prepared"
    assert second.hostname == f"org-{item.pk.hex}.owndesk.work"
    assert len(cloud["tunnels"]) == len(cloud["records"]) == 1
    response = client_for(desk["admin"]).get("/api/v1/portal/installations")
    public = response.json()["results"][0]["public_route"]
    assert public == {"state": "prepared", "hostname": second.hostname, "public_access": False}
    assert "private-" not in json.dumps(response.json())
    assert client_for(desk["second"]).get("/api/v1/portal/installations").json()["results"] == []


@pytest.mark.django_db
def test_lost_dns_response_reuses_remote_resources_and_checkpoint(desk, cloud):
    item = installation(desk)
    cloud["fail_dns"] = True
    with pytest.raises(TunnelError) as error:
        prepare_route(item.pk)
    assert "private" not in str(error.value)
    route = InstallationRoute.objects.get(installation=item)
    assert route.tunnel_id and route.state == "error"
    assert prepare_route(item.pk).state == "prepared"
    assert len(cloud["tunnels"]) == len(cloud["records"]) == 1


@pytest.mark.django_db
def test_lost_tunnel_response_is_recovered_by_deterministic_name(desk, cloud):
    item = installation(desk)
    tunnel_id = str(uuid4())
    cloud["tunnels"].append({"id": tunnel_id, "name": "owndesk-" + str(item.pk), "remote_config": True})
    assert str(prepare_route(item.pk).tunnel_id) == tunnel_id
    assert len(cloud["tunnels"]) == 1


@pytest.mark.django_db
def test_dns_conflict_is_not_overwritten(desk, cloud):
    item = installation(desk)
    cloud["records"].append({"id": "unrelated", "type": "A", "content": "192.0.2.1"})
    with pytest.raises(TunnelError, match="занято"):
        prepare_route(item.pk)
    assert cloud["records"][0]["id"] == "unrelated"
    assert not any(method in ("DELETE", "PUT") and "/dns_records" in path for method, path in cloud["calls"])


@pytest.mark.django_db
def test_revoke_blocks_before_dns_delete_and_failure_does_not_confirm(desk, client_for, cloud):
    item = installation(desk)
    prepare_route(item.pk)
    owner = client_for(desk["admin"])
    cloud["fail_block"] = True
    endpoint = f"/api/v1/portal/installations/{item.pk}/revoke"
    response = owner.post(endpoint)
    assert response.status_code == 400
    assert "never-display" not in str(response.content)
    item.refresh_from_db()
    assert item.revoked_at is None and len(cloud["records"]) == 1
    cloud["fail_block"] = False
    assert owner.post(endpoint).status_code == 200
    assert cloud["records"] == []
    assert cloud["calls"][-2][0] == "PUT" and cloud["calls"][-1][0] == "DELETE"
    assert InstallationRoute.objects.get(installation=item).state == "revoked"
    assert owner.post(endpoint).status_code == 200
    with pytest.raises(TunnelError, match="активную"):
        prepare_route(item.pk)


@pytest.mark.django_db
def test_preparation_requires_portal_credentials_and_pairing(desk, cloud, settings):
    item = installation(desk)
    settings.DEPLOYMENT_MODE = "organization"
    with pytest.raises(CommandError, match="портале"):
        call_command("prepare_public_route", str(item.pk))
    settings.DEPLOYMENT_MODE = "portal"
    settings.CLOUDFLARE_API_TOKEN = ""
    with pytest.raises(TunnelError, match="реквизиты"):
        prepare_route(item.pk)
    settings.CLOUDFLARE_API_TOKEN = "private-account-token"
    item.paired_at = None
    item.save()
    with pytest.raises(TunnelError, match="привяжите"):
        prepare_route(item.pk)
    assert not cloud["calls"] and not InstallationRoute.objects.exists()


@pytest.mark.django_db
def test_connector_automatically_prepares_route_and_recovers_partial_failure(desk, cloud):
    item = installation(desk)
    key = "k" * 64
    item.connector_hash = digest(key)
    item.save()
    client = APIClient()
    endpoint = f"/api/v1/portal/installations/{item.pk}/configuration"
    assert client.post(endpoint).status_code == 403
    assert not cloud["calls"]
    cloud["fail_dns"] = True
    first = client.post(endpoint, HTTP_AUTHORIZATION="Bearer " + key)
    assert first.status_code == 400
    route = InstallationRoute.objects.get(installation=item)
    assert route.state == "error" and route.tunnel_id
    second = client.post(endpoint, HTTP_AUTHORIZATION="Bearer " + key)
    assert second.status_code == 200
    assert second["Cache-Control"] == "no-store"
    route.refresh_from_db()
    assert route.state == "checking"
    assert len(cloud["tunnels"]) == len(cloud["records"]) == 1
    assert second.json()["hostname"] == route.hostname
    assert client.post(endpoint, HTTP_AUTHORIZATION="Bearer " + key).status_code == 200
    assert len(cloud["tunnels"]) == len(cloud["records"]) == 1
    calls = len(cloud["calls"])
    route.state = "revoked"
    route.save()
    assert client.post(endpoint, HTTP_AUTHORIZATION="Bearer " + key).status_code == 403
    assert len(cloud["calls"]) == calls
