import re
import time
from datetime import timedelta
from types import SimpleNamespace
from urllib.parse import urlparse, parse_qs
from unittest.mock import patch
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from django.test import override_settings
from django.utils import timezone
from rest_framework.test import APIClient
from apps.accounts.models import User, Registration
from apps.accounts.tasks import send_registration_mail
from apps.accounts.google import verify_google_token
from apps.accounts.registration import request_source
from apps.workspaces.models import Membership, Workspace


@pytest.mark.parametrize(("trusted", "peer", "header", "expected"), [
    (False, "127.0.0.1", "203.0.113.8", "127.0.0.1"),
    (True, "192.0.2.3", "203.0.113.8", "192.0.2.3"),
    (True, "127.0.0.1", "2001:0db8::1", "2001:db8::1"),
    (True, "::1", "203.0.113.8, 203.0.113.9", "::1"),
    (True, "::1", "fe80::1%test", "::1"),
])
def test_public_auth_uses_only_single_ip_from_trusted_loopback(settings, trusted, peer, header, expected):
    settings.TRUST_LOOPBACK_CLIENT_IP = trusted
    request = SimpleNamespace(META={"REMOTE_ADDR": peer, "HTTP_X_OWNDESK_CLIENT_IP": header})
    assert request_source(request) == expected


@pytest.fixture(autouse=True)
def configuration(settings):
    settings.LOCAL_EMAIL_PREVIEW = False
    settings.PUBLIC_REGISTRATION_ENABLED = True
    settings.PUBLIC_BASE_URL = "https://desk.test"
    settings.EMAIL_HOST = "smtp.test"
    settings.DEFAULT_FROM_EMAIL = "desk@example.test"
    settings.EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
    settings.GOOGLE_CLIENT_ID = "client-test"
    settings.GOOGLE_CLIENT_SECRET = "secret-test"


def register(client):
    response = client.post("/api/v1/auth/register", {"name": "Новый пользователь", "email": "New@example.test"}, format="json")
    assert response.status_code == 202
    return Registration.objects.get(email="new@example.test")


@pytest.mark.django_db
def test_verified_email_creates_isolated_workspace_and_can_login(desk):
    client = APIClient()
    reg = register(client)
    assert not User.objects.filter(email=reg.email).exists()
    token = re.search(r"#verify=([^\s]+)", reg.mail_body)[1]
    send_registration_mail()
    from django.core import mail
    assert len(mail.outbox) == 1 and token in mail.outbox[0].body
    reg.refresh_from_db()
    assert reg.mail_state == "sent" and reg.mail_body == ""
    response = client.post("/api/v1/auth/verify", {"token": token, "password": "Unique-testing-password-2026"}, format="json")
    assert response.status_code == 200
    user = User.objects.get(email=reg.email)
    member = Membership.objects.get(user=user)
    assert user.email_verified and member.role == "admin"
    assert member.workspace_id != desk["ws"].pk
    assert client.get("/api/v1/tickets").json()["count"] == 0
    assert client.get(f'/api/v1/tickets/{desk["ticket"].pk}').status_code == 404
    assert client.post("/api/v1/auth/verify", {"token": token, "password": "Another-password-2026"}, format="json").status_code == 400
    client.logout()
    assert client.post("/api/v1/auth/login", {"username": reg.email.upper(), "password": "Unique-testing-password-2026"}, format="json").status_code == 200


@pytest.mark.django_db
def test_expiry_throttle_and_no_automatic_email_retry():
    client = APIClient(); reg = register(client)
    assert client.post("/api/v1/auth/register", {"name": "Again", "email": reg.email}, format="json").status_code == 429
    with patch("apps.accounts.tasks.send_mail", side_effect=OSError("SMTP unavailable")) as send:
        send_registration_mail(); send_registration_mail()
        assert send.call_count == 1
    reg.refresh_from_db(); assert reg.mail_state == "unknown"
    reg.expires_at = timezone.now() - timedelta(minutes=1); reg.save()
    assert client.post("/api/v1/auth/verify", {"token": str(reg.pk) + ".wrong", "password": "Unique-password-2026"}, format="json").status_code == 400


@pytest.mark.django_db
def test_csrf_and_disabled_registration(settings):
    client = APIClient(enforce_csrf_checks=True)
    assert client.post("/api/v1/auth/register", {"email": "a@example.test", "name": "a"}).status_code == 403
    settings.PUBLIC_REGISTRATION_ENABLED = False
    assert APIClient().post("/api/v1/auth/register", {"email": "a@example.test", "name": "a"}).status_code == 403
    assert not APIClient().get("/api/v1/auth/options").json()["email_registration"]


@pytest.fixture
def signing_key():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def claims(expected_nonce, **overrides):
    return {"iss": "https://accounts.google.com", "sub": "google-test-id", "aud": "client-test", "iat": int(time.time()),
            "exp": int(time.time()) + 3600, "nonce": expected_nonce, "email": "google@example.test", "email_verified": True,
            "name": "Google User", **overrides}


@pytest.mark.parametrize("overrides", [{"aud": "wrong"}, {"exp": 1}, {"iss": "https://attacker.test"}, {"nonce": "wrong"}, {"email_verified": False}, {"azp": "wrong"}])
def test_google_rejects_invalid_identity(signing_key, overrides):
    token = jwt.encode(claims("nonce", **overrides), signing_key, algorithm="RS256")
    with patch("apps.accounts.google.keys.get_signing_key_from_jwt", return_value=SimpleNamespace(key=signing_key.public_key())):
        with pytest.raises((jwt.PyJWTError, ValueError)):
            verify_google_token(token, "nonce")


@pytest.mark.django_db
def test_google_registration_repeat_and_state(signing_key):
    client = APIClient()
    for _ in range(2):
        redirect = client.get("/api/v1/auth/google/start")
        params = parse_qs(urlparse(redirect.url).query)
        assert params["code_challenge_method"] == ["S256"]
        token = jwt.encode(claims(params["nonce"][0]), signing_key, algorithm="RS256")
        with patch("apps.accounts.google.keys.get_signing_key_from_jwt", return_value=SimpleNamespace(key=signing_key.public_key())), patch("apps.accounts.google.httpx.post") as exchange:
            exchange.return_value.json.return_value = {"id_token": token}
            result = client.get("/api/v1/auth/google/callback", {"state": params["state"][0], "code": "code"})
            assert result.url == "/tickets"
            assert "code_verifier" in exchange.call_args.kwargs["data"]
        client.logout()
    assert User.objects.filter(google_sub="google-test-id").count() == 1
    assert Workspace.objects.count() == 1
    with patch("apps.accounts.google.httpx.post") as exchange:
        assert "auth_error" in client.get("/api/v1/auth/google/callback", {"state": "invalid", "code": "code"}).url
        exchange.assert_not_called()


@pytest.mark.django_db
def test_google_never_links_existing_email(signing_key):
    User.objects.create_user(username="existing", email="google@example.test", password="existing-password")
    client=APIClient(); params=parse_qs(urlparse(client.get("/api/v1/auth/google/start").url).query)
    token=jwt.encode(claims(params["nonce"][0]), signing_key, algorithm="RS256")
    with patch("apps.accounts.google.keys.get_signing_key_from_jwt", return_value=SimpleNamespace(key=signing_key.public_key())), patch("apps.accounts.google.httpx.post") as exchange:
        exchange.return_value.json.return_value={"id_token":token}
        assert client.get("/api/v1/auth/google/callback", {"state":params["state"][0], "code":"code"}).url == "/?auth_error=existing_account"
    assert Workspace.objects.count() == 0


@pytest.mark.django_db
def test_local_preview_never_becomes_verified_production_account(settings):
    settings.DEBUG = True
    settings.LOCAL_EMAIL_PREVIEW = True
    settings.EMAIL_HOST = ""
    client = APIClient()
    response = client.post("/api/v1/auth/register", {"name": "Local test", "email": "local@example.test"}, format="json")
    assert response.status_code == 202
    token = response.json()["preview_link"].split("#verify=")[1]
    password = "Local-password-2026"
    assert client.post("/api/v1/auth/verify", {"token": token, "password": password}, format="json").status_code == 200
    user = User.objects.get(email="local@example.test")
    assert user.is_local_account and not user.email_verified
    client.logout()
    assert client.post("/api/v1/auth/login", {"username": user.email, "password": password}, format="json").status_code == 200
    client.logout()
    settings.LOCAL_EMAIL_PREVIEW = False
    assert client.post("/api/v1/auth/login", {"username": user.username, "password": password}, format="json").status_code == 403


@pytest.mark.django_db
def test_local_preview_rejects_remote_request(settings):
    settings.DEBUG = True
    settings.LOCAL_EMAIL_PREVIEW = True
    client = APIClient()
    response = client.post("/api/v1/auth/register", {"name": "Remote", "email": "remote@example.test"}, format="json", REMOTE_ADDR="203.0.113.1")
    assert response.status_code == 403
    assert not Registration.objects.exists()
