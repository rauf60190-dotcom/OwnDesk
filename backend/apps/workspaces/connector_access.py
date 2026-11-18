"""Connector authentication, origin identity proof and staged public activation."""
import hashlib
import hmac
import json
import secrets
from datetime import timedelta
from pathlib import Path

import httpx
from django.conf import settings
from django.db import transaction
from django.http import Http404
from django.utils import timezone
from drf_spectacular.utils import extend_schema
from rest_framework.exceptions import AuthenticationFailed, ValidationError
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.registration import digest
from apps.accounts.security import charge
from .models import ConnectedInstallation, Workspace, InstallationSetup
from .tunnels import Cloudflare, TunnelError, prepare_route


def signature(key, installation_id, hostname, challenge, ready):
    message = json.dumps([str(installation_id), hostname, challenge, ready], separators=(",", ":"))
    return hmac.new(key.encode(), message.encode(), hashlib.sha256).hexdigest()


def public_ready(hostname):
    origin = "https://" + hostname
    return bool(
        settings.DEPLOYMENT_MODE == "organization" and not settings.DEBUG
        and len(settings.SECRET_KEY) >= 50 and len(set(settings.SECRET_KEY)) >= 5
        and settings.SESSION_COOKIE_SECURE and settings.CSRF_COOKIE_SECURE
        and settings.SECURE_SSL_REDIRECT
        and settings.PUBLIC_BASE_URL == origin
        and hostname in settings.ALLOWED_HOSTS and "*" not in settings.ALLOWED_HOSTS
        and origin in settings.CSRF_TRUSTED_ORIGINS
        and settings.DATABASES["default"]["ENGINE"] == "django.db.backends.postgresql"
    )


def authenticated_installation(request, pk, *, require_route=True):
    if settings.DEPLOYMENT_MODE != "portal":
        raise Http404
    scheme, _, key = request.headers.get("Authorization", "").partition(" ")
    if scheme != "Bearer" or not 43 <= len(key) <= 128:
        raise AuthenticationFailed("Нет доступа к подключению.")
    item = ConnectedInstallation.objects.select_for_update().filter(pk=pk, owner__is_active=True).first()
    if (not item or not item.paired_at or item.revoked_at
            or not secrets.compare_digest(item.connector_hash, digest(key))):
        raise AuthenticationFailed("Подключение отключено или недействительно.")
    route = getattr(item, "route", None)
    if route and route.state == "revoked":
        raise AuthenticationFailed("Публичный маршрут отозван.")
    if require_route and (not route or route.state not in {"prepared", "checking", "active"}):
        raise ValidationError("Администратору портала нужно подготовить публичный маршрут.")
    expected_hostname = "org-" + item.pk.hex + "." + settings.INSTALLATIONS_DOMAIN
    if route and route.hostname != expected_hostname:
        raise ValidationError("Адрес маршрута не соответствует установке.")
    return item, route, key


def route_available(item, route):
    cutoff = timezone.now() - timedelta(seconds=90)
    return bool(not item.revoked_at and route and route.state == "active"
                and route.verified_at and route.verified_at > cutoff
                and item.last_seen_at and item.last_seen_at > cutoff)


def charge_connector(request, pk, action):
    # Commit the limiter before the operation transaction: a failed proof must
    # not roll back its attempt counter. Recheck credentials after charging.
    with transaction.atomic():
        authenticated_installation(request, pk, require_route=action != "config")
    charge("connector-" + action + ":" + str(pk), limit=12, minutes=1)


class ConnectorConfiguration(APIView):
    permission_classes = [AllowAny]
    authentication_classes = []

    @extend_schema(request=None, responses={200: {"type": "object"}})
    def post(self, request, pk):
        charge_connector(request, pk, "config")
        with transaction.atomic():
            _, existing, _ = authenticated_installation(request, pk, require_route=False)
            needs_preparation = existing is None or existing.state in {"pending", "error"}
        if needs_preparation:
            try:
                # Run outside the response transaction so remote checkpoints
                # survive a partial failure and can be recovered on retry.
                prepare_route(pk)
            except TunnelError:
                raise ValidationError("Адрес пока не подготовлен. Коннектор повторит попытку; проверьте настройки Cloudflare на портале.") from None
        with transaction.atomic():
            _, route, _ = authenticated_installation(request, pk)
            provider = provider_for_request()
            try:
                token = provider.token(route)
                if route.state == "prepared":
                    provider.configure(route)
                    route.state = "checking"
                    route.save()
            except TunnelError:
                raise ValidationError("Не удалось получить конфигурацию туннеля. Повторите попытку.") from None
            finally:
                provider.close()
        return Response({"hostname": route.hostname, "token": token}, headers={"Cache-Control": "no-store"})


class ConnectorActivation(APIView):
    permission_classes = [AllowAny]
    authentication_classes = []

    @extend_schema(request=None, responses={200: {"type": "object"}})
    def post(self, request, pk):
        charge_connector(request, pk, "activate")
        with transaction.atomic():
            item, route, key = authenticated_installation(request, pk)
            challenge = secrets.token_urlsafe(32)
            try:
                # URL comes only from the portal's assigned hostname, never request data.
                with httpx.Client(timeout=5, follow_redirects=False, trust_env=False) as client:
                    response = client.get("https://" + route.hostname + "/api/v1/connector/proof",
                                          params={"challenge": challenge})
                    response.raise_for_status()
                    data = response.json()
                expected = signature(key, item.pk, route.hostname, challenge, True)
                if not isinstance(data, dict) or not secrets.compare_digest(str(data.get("proof", "")), expected):
                    raise ValueError("Origin not ready")
            except (httpx.HTTPError, ValueError, TypeError):
                raise ValidationError("Публичный HTTPS-адрес пока не подтвердил готовность установки.") from None
            provider = provider_for_request()
            try:
                if route.state != "active":
                    provider.configure(route, full=True)
                route.state = "active"
                route.verified_at = timezone.now()
                route.save()
                item.last_seen_at = timezone.now()
                item.save(update_fields=["last_seen_at"])
            except TunnelError:
                raise ValidationError("Не удалось включить публичный маршрут. Повторите проверку.") from None
            finally:
                provider.close()
        return Response({"public_access": True, "public_url": "https://" + route.hostname},
                        headers={"Cache-Control": "no-store"})


class ConnectorProof(APIView):
    permission_classes = [AllowAny]
    authentication_classes = []

    @extend_schema(responses={200: {"type": "object"}})
    def get(self, request):
        if settings.DEPLOYMENT_MODE != "organization" or not settings.PORTAL_CONNECTOR_STATE_FILE:
            raise Http404
        challenge = request.query_params.get("challenge", "")
        if len(challenge) != 43 or not all(char.isascii() and (char.isalnum() or char in "_-") for char in challenge):
            raise ValidationError("Недействительная проверка подключения.")
        try:
            state = json.loads(Path(settings.PORTAL_CONNECTOR_STATE_FILE).read_text(encoding="utf-8"))
            key, installation_id = state["connector_key"], state["id"]
            if not isinstance(key, str) or not 43 <= len(key) <= 128:
                raise ValueError("Invalid key")
        except (OSError, ValueError, TypeError, KeyError):
            raise Http404 from None
        hostname = request.get_host().split(":", 1)[0]
        bootstrap_ready = InstallationSetup.objects.filter(
            pk=1, completed_at=None, expires_at__gt=timezone.now(),
        ).exclude(token_hash="").exists()
        ready = public_ready(hostname) and (Workspace.objects.exists() or bootstrap_ready)
        return Response({"proof": signature(key, installation_id, hostname, challenge, ready)},
                        headers={"Cache-Control": "no-store"})


def provider_for_request():
    try:
        return Cloudflare()
    except TunnelError:
        raise ValidationError("Администратору портала нужно настроить Cloudflare.") from None
