from datetime import timedelta
import json
from unittest.mock import MagicMock, patch
from uuid import uuid4

import httpx
import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts.registration import digest
from apps.workspaces.connector_access import public_ready, signature
from apps.workspaces.models import ConnectedInstallation, InstallationRoute
from apps.workspaces.tunnels import prepare_route


@pytest.fixture
def activation(desk, settings):
    settings.DEPLOYMENT_MODE = "portal"
    settings.CLOUDFLARE_API_TOKEN = "account-secret"
    settings.CLOUDFLARE_ACCOUNT_ID = "a" * 32
    settings.CLOUDFLARE_ZONE_ID = "b" * 32
    key = "k" * 64
    item = ConnectedInstallation.objects.create(
        owner=desk["admin"].user, name="Company", code_hash=uuid4().hex,
        code_expires_at=timezone.now(), paired_at=timezone.now(), connector_hash=digest(key),
    )
    route = InstallationRoute.objects.create(
        installation=item, hostname="org-" + item.pk.hex + ".owndesk.work",
        tunnel_id=uuid4(), dns_record_id="d" * 32, state="prepared",
    )
    state = {"item": item, "route": route, "key": key, "rules": [], "ready": True, "redirect": False}
    client_type = httpx.Client

    def respond(request):
        if request.url.host == "api.cloudflare.com":
            assert request.headers["Authorization"] == "Bearer account-secret"
            if request.url.path.endswith("/token"):
                result = "dedicated-tunnel-token-" + "x" * 40
            elif request.url.path.endswith("/configurations"):
                state["rules"].append(json.loads(request.content)["config"]["ingress"])
                result = {}
            else:
                raise AssertionError(str(request.url))
            return httpx.Response(200, json={"success": True, "result": result})
        assert request.url.host == route.hostname
        assert "authorization" not in request.headers
        if state["redirect"]:
            return httpx.Response(302, headers={"Location": "https://owndesk.work"})
        proof = signature(key, item.pk, route.hostname, request.url.params["challenge"], state["ready"])
        return httpx.Response(200, json={"proof": proof})

    with patch("apps.workspaces.connector_access.httpx.Client", side_effect=lambda **kwargs: client_type(
        **kwargs, transport=httpx.MockTransport(respond),
    )):
        yield state


@pytest.mark.django_db
def test_configuration_is_scoped_and_no_full_route_before_https_proof(activation, client_for, desk):
    item, route, key = (activation[name] for name in ("item", "route", "key"))
    guest = APIClient()
    endpoint = f"/api/v1/portal/installations/{item.pk}"
    assert guest.post(endpoint + "/configuration").status_code == 403
    assert client_for(desk["admin"]).post(endpoint + "/configuration").status_code == 403
    assert guest.post(endpoint + "/configuration", HTTP_AUTHORIZATION="Bearer " + "wrong" * 16).status_code == 403
    response = guest.post(endpoint + "/configuration", HTTP_AUTHORIZATION="Bearer " + key)
    assert response.status_code == 200 and response["Cache-Control"] == "no-store"
    assert response.json()["hostname"] == route.hostname
    assert "dedicated-tunnel-token" in response.json()["token"]
    assert "account-secret" not in str(response.content)
    assert activation["rules"][-1][0]["path"] == "^/api/v1/connector/proof$"
    route.refresh_from_db()
    assert route.state == "checking" and route.verified_at is None
    activation["ready"] = False
    assert guest.post(endpoint + "/activate", HTTP_AUTHORIZATION="Bearer " + key).status_code == 400
    assert len(activation["rules"]) == 1
    activation["ready"] = True
    activation["redirect"] = True
    assert guest.post(endpoint + "/activate", HTTP_AUTHORIZATION="Bearer " + key).status_code == 400
    assert len(activation["rules"]) == 1
    activation["redirect"] = False
    result = guest.post(endpoint + "/activate", HTTP_AUTHORIZATION="Bearer " + key)
    assert result.status_code == 200 and result.json()["public_access"] is True
    assert "path" not in activation["rules"][-1][0]
    assert activation["rules"][-1][0]["service"] == "http://127.0.0.1:5173"
    # Retries after activation do not revert a live route to 404 / proof-only.
    count = len(activation["rules"])
    assert guest.post(endpoint + "/configuration", HTTP_AUTHORIZATION="Bearer " + key).status_code == 200
    assert prepare_route(item.pk).state == "active"
    assert len(activation["rules"]) == count
    status = client_for(desk["admin"]).get("/api/v1/portal/installations").json()["results"][0]
    assert status["public_route"]["public_access"] is True
    InstallationRoute.objects.filter(pk=route.pk).update(verified_at=timezone.now() - timedelta(minutes=2))
    status = client_for(desk["admin"]).get("/api/v1/portal/installations").json()["results"][0]
    assert status["public_route"]["public_access"] is False
    item.revoked_at = timezone.now()
    item.save()
    assert guest.post(endpoint + "/configuration", HTTP_AUTHORIZATION="Bearer " + key).status_code == 403
    assert guest.post(endpoint + "/activate", HTTP_AUTHORIZATION="Bearer " + key).status_code == 403


@pytest.mark.django_db
def test_proof_binds_challenge_host_installation_and_readiness(desk, settings, tmp_path):
    settings.DEPLOYMENT_MODE = "organization"
    settings.PORTAL_CONNECTOR_STATE_FILE = str(tmp_path / "state.json")
    item_id = str(uuid4())
    key = "k" * 64
    (tmp_path / "state.json").write_text(json.dumps({"id": item_id, "connector_key": key}))
    guest = APIClient()
    challenge = "c" * 43
    endpoint = "/api/v1/connector/proof"
    assert guest.get(endpoint).status_code == 400
    response = guest.get(endpoint, {"challenge": challenge})
    assert response.status_code == 200
    assert response.json()["proof"] == signature(key, item_id, "testserver", challenge, False)
    assert key not in str(response.content)
    with patch("apps.workspaces.connector_access.public_ready", return_value=True):
        response = guest.get(endpoint, {"challenge": challenge})
    assert response.json()["proof"] == signature(key, item_id, "testserver", challenge, True)
    assert response.json()["proof"] != signature(key, uuid4(), "testserver", challenge, True)
    settings.DEPLOYMENT_MODE = "portal"
    assert guest.get(endpoint, {"challenge": challenge}).status_code == 404


def test_secure_runtime_requires_all_settings(settings):
    hostname = "org-example.owndesk.work"
    settings.DEPLOYMENT_MODE = "organization"
    settings.DEBUG = False
    settings.SECRET_KEY = "g7H_9Q-afp2" * 6
    settings.SESSION_COOKIE_SECURE = settings.CSRF_COOKIE_SECURE = True
    settings.SECURE_SSL_REDIRECT = True
    settings.PUBLIC_BASE_URL = "https://" + hostname
    settings.ALLOWED_HOSTS = [hostname]
    settings.CSRF_TRUSTED_ORIGINS = [settings.PUBLIC_BASE_URL]
    with patch.dict(settings.DATABASES["default"], ENGINE="django.db.backends.postgresql"):
        assert public_ready(hostname)
        settings.DEBUG = True
        assert not public_ready(hostname)
        settings.DEBUG = False
        settings.ALLOWED_HOSTS = ["*", hostname]
        assert not public_ready(hostname)
        settings.ALLOWED_HOSTS = [hostname]
        settings.PUBLIC_BASE_URL = "http://127.0.0.1:5173"
        assert not public_ready(hostname)


def test_public_connector_refuses_development_server(settings, tmp_path):
    settings.DEBUG = True
    settings.DEPLOYMENT_MODE = "organization"
    with pytest.raises(CommandError, match="DEBUG=0"):
        call_command("run_connector", state=str(tmp_path / "state.json"))


@pytest.mark.django_db
def test_failed_origin_proofs_are_rate_limited(activation):
    activation["ready"] = False
    guest = APIClient()
    endpoint = f"/api/v1/portal/installations/{activation['item'].pk}/activate"
    for _ in range(12):
        assert guest.post(endpoint, HTTP_AUTHORIZATION="Bearer " + activation["key"]).status_code == 400
    assert guest.post(endpoint, HTTP_AUTHORIZATION="Bearer " + activation["key"]).status_code == 429
    assert activation["rules"] == []


@pytest.mark.django_db
def test_public_bootstrap_needs_valid_grant_and_owner_creation_still_needs_code(settings, tmp_path):
    from apps.workspaces.models import InstallationSetup, Workspace
    settings.DEPLOYMENT_MODE = "organization"
    settings.PORTAL_CONNECTOR_STATE_FILE = str(tmp_path / "state.json")
    item = uuid4()
    key, code, challenge = "k" * 64, "s" * 43, "c" * 43
    (tmp_path / "state.json").write_text(json.dumps({"id": str(item), "connector_key": key}))
    grant = InstallationSetup.objects.create(token_hash=digest(code), expires_at=timezone.now() + timedelta(hours=1))
    guest = APIClient()
    endpoint = "/api/v1/connector/proof"
    with patch("apps.workspaces.connector_access.public_ready", return_value=True):
        result = guest.get(endpoint, {"challenge": challenge})
        assert result.json()["proof"] == signature(key, item, "testserver", challenge, True)
        grant.expires_at = timezone.now() - timedelta(seconds=1)
        grant.save()
        result = guest.get(endpoint, {"challenge": challenge})
        assert result.json()["proof"] == signature(key, item, "testserver", challenge, False)
    grant.expires_at = timezone.now() + timedelta(hours=1)
    grant.save()
    payload = {"organization": "Bootstrap", "name": "Owner", "email": "bootstrap@example.test",
               "password": "Bootstrap-owner-test-password", "timezone": "UTC", "code": "wrong" * 10}
    assert guest.post("/api/v1/setup/complete", payload, format="json").status_code == 400
    assert not Workspace.objects.exists()
    payload["code"] = code
    assert guest.post("/api/v1/setup/complete", payload, format="json").status_code == 200
    assert Workspace.objects.count() == 1
