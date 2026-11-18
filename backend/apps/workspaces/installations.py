import secrets
from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.http import Http404
from django.utils import timezone
from drf_spectacular.utils import extend_schema
from rest_framework import serializers
from rest_framework.exceptions import AuthenticationFailed, ValidationError
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.registration import digest, request_source
from apps.accounts.security import charge
from .models import ConnectedInstallation
from .tunnels import revoke_route, TunnelError
from .connector_access import route_available


class InstallationName(serializers.Serializer):
    name = serializers.CharField(max_length=200)


class PairInstallation(serializers.Serializer):
    code = serializers.CharField(min_length=32, max_length=128, write_only=True)
    connector_key = serializers.CharField(min_length=43, max_length=128, write_only=True)
    version = serializers.RegexField(r"^\d+\.\d+\.\d+$", max_length=32)


class InstallationStatus(serializers.ModelSerializer):
    connection_status = serializers.SerializerMethodField()
    public_route = serializers.SerializerMethodField()

    class Meta:
        model = ConnectedInstallation
        fields = ["id", "name", "created_at", "code_expires_at", "paired_at", "version",
                  "last_seen_at", "revoked_at", "connection_status", "public_route"]

    def get_public_route(self, item) -> dict:
        route = getattr(item, "route", None)
        return {"state": route.state if route else "not_configured",
                "hostname": route.hostname if route else "", "public_access": route_available(item, route)}

    def get_connection_status(self, item) -> str:
        if item.revoked_at:
            return "revoked"
        if not item.paired_at:
            return "waiting" if item.code_expires_at > timezone.now() else "expired"
        if item.last_seen_at and item.last_seen_at > timezone.now() - timedelta(seconds=90):
            return "connected"
        return "offline"


def require_portal():
    if settings.DEPLOYMENT_MODE != "portal":
        raise Http404


class InstallationsView(APIView):
    @extend_schema(responses={200: InstallationStatus(many=True)})
    def get(self, request):
        require_portal()
        items = ConnectedInstallation.objects.filter(owner=request.user).select_related("route").order_by("-created_at")
        return Response({"results": InstallationStatus(items, many=True).data})

    @extend_schema(request=InstallationName, responses={201: {"type": "object"}})
    def post(self, request):
        require_portal()
        form = InstallationName(data=request.data)
        form.is_valid(raise_exception=True)
        charge("installation-create:" + str(request.user.pk), limit=10, minutes=60)
        code = secrets.token_urlsafe(32)
        item = ConnectedInstallation.objects.create(
            owner=request.user, name=form.validated_data["name"], code_hash=digest(code),
            code_expires_at=timezone.now() + timedelta(minutes=15),
        )
        return Response({**InstallationStatus(item).data, "pairing_code": code},
                        status=201, headers={"Cache-Control": "no-store"})


class RevokeInstallation(APIView):
    @extend_schema(request=None, responses={200: {"type": "object"}})
    def post(self, request, pk):
        require_portal()
        with transaction.atomic():
            item = ConnectedInstallation.objects.select_for_update().filter(pk=pk, owner=request.user).first()
            if not item:
                raise Http404
            if not item.revoked_at:
                try:
                    revoke_route(item)
                except TunnelError:
                    raise ValidationError("Не удалось отключить публичный маршрут. Привязка не отозвана; повторите попытку.") from None
                item.revoked_at = timezone.now()
                item.save(update_fields=["revoked_at"])
        return Response({"ok": True})


class PairInstallationView(APIView):
    permission_classes = [AllowAny]
    authentication_classes = []

    @extend_schema(request=PairInstallation, responses={200: {"type": "object"}})
    def post(self, request):
        require_portal()
        charge("installation-pair:" + request_source(request), limit=30)
        form = PairInstallation(data=request.data)
        form.is_valid(raise_exception=True)
        values = form.validated_data
        key_hash = digest(values["connector_key"])
        with transaction.atomic():
            item = ConnectedInstallation.objects.select_for_update().filter(
                code_hash=digest(values["code"]), owner__is_active=True,
            ).first()
            if not item or item.revoked_at:
                raise ValidationError("Код недействителен. Создайте подключение заново.")
            if item.paired_at:
                if not secrets.compare_digest(item.connector_hash, key_hash):
                    raise ValidationError("Код уже использован другой установкой.")
            else:
                if item.code_expires_at <= timezone.now():
                    raise ValidationError("Срок кода истёк. Создайте подключение заново.")
                item.paired_at = timezone.now()
                item.connector_hash = key_hash
            item.last_seen_at = timezone.now()
            item.version = values["version"]
            item.save(update_fields=["paired_at", "connector_hash", "last_seen_at", "version"])
        return Response({"id": item.pk, "name": item.name}, headers={"Cache-Control": "no-store"})


class InstallationHeartbeat(APIView):
    permission_classes = [AllowAny]
    authentication_classes = []

    @extend_schema(request=None, responses={200: {"type": "object"}})
    def post(self, request, pk):
        require_portal()
        authorization = request.headers.get("Authorization", "")
        scheme, _, key = authorization.partition(" ")
        if scheme != "Bearer" or not 43 <= len(key) <= 128:
            raise AuthenticationFailed("Нет доступа к подключению.")
        with transaction.atomic():
            item = ConnectedInstallation.objects.select_for_update().filter(pk=pk, owner__is_active=True).first()
            if (not item or not item.paired_at or item.revoked_at
                    or not secrets.compare_digest(item.connector_hash, digest(key))):
                raise AuthenticationFailed("Подключение отключено или недействительно.")
            item.last_seen_at = timezone.now()
            item.save(update_fields=["last_seen_at"])
        return Response({"ok": True})
