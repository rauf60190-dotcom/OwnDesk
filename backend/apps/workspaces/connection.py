import json
import secrets
import time
from io import StringIO
from pathlib import Path
from uuid import UUID

from django.conf import settings
from django.core.management import call_command
from django.core.management.base import CommandError
from django.http import Http404
from drf_spectacular.utils import extend_schema
from rest_framework import serializers
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.access import member
from local_snapshot import SnapshotError, local_runtime_lock
from .management.commands.connect_portal import Command
from .connector_access import public_ready


class ConnectionCode(serializers.Serializer):
    code = serializers.CharField(min_length=32, max_length=128, write_only=True, required=False)
    prepare_address = serializers.BooleanField(default=False)

    def validate(self, values):
        if not values.get("code") and not values.get("prepare_address"):
            raise serializers.ValidationError("Укажите код подключения или запросите подготовку адреса.")
        return values


def state_path():
    return Path(settings.PORTAL_CONNECTOR_STATE_FILE).resolve()


def private_state(target):
    if not target.exists():
        return {}
    data = json.loads(target.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("Invalid connector state")
    return data


class OrganizationConnection(APIView):
    def check_owner(self, request):
        if settings.DEPLOYMENT_MODE != "organization":
            raise Http404
        if not member(request).is_owner:
            raise PermissionDenied("Подключение настраивает владелец организации.")

    @extend_schema(responses={200: {"type": "object"}})
    def get(self, request):
        self.check_owner(request)
        configured = bool(settings.PORTAL_CONNECTOR_STATE_FILE)
        try:
            data = private_state(state_path()) if configured else {}
            paired = configured and bool(data.get("id"))
        except (OSError, ValueError):
            raise ValidationError("Не удалось прочитать настройки подключения.") from None
        result = {"available": configured, "paired": paired, "public_access": False}
        if configured and paired:
            try:
                expected = "org-" + UUID(str(data["id"])).hex + ".owndesk.work"
                if data.get("public_hostname") == expected:
                    result["prepared_hostname"] = expected
            except (ValueError, TypeError):
                pass
            status_path = state_path().with_name(state_path().name + ".status.json")
            try:
                status = private_state(status_path)
                fresh = 0 <= time.time() - float(status.get("checked_at", 0)) < 90
                state = status.get("state", "not_started")
                if not isinstance(state, str) or state not in {"checking", "active", "error", "revoked", "stopped", "not_started"}:
                    state = "error"
                result["connector_status"] = state if fresh else "offline"
                origin = status.get("public_url", "")
                if (fresh and state == "active" and isinstance(origin, str) and origin.startswith("https://")
                        and origin == settings.PUBLIC_BASE_URL and public_ready(origin.removeprefix("https://"))):
                    result.update(public_access=True, public_url=origin)
            except (OSError, ValueError, TypeError):
                result["connector_status"] = "offline"
        return Response(result,
                        headers={"Cache-Control": "no-store"})

    @extend_schema(request=ConnectionCode, responses={200: {"type": "object"}})
    def post(self, request):
        self.check_owner(request)
        if not settings.PORTAL_CONNECTOR_STATE_FILE:
            raise ValidationError("Укажите закрытый файл настроек коннектора на сервере.")
        form = ConnectionCode(data=request.data)
        form.is_valid(raise_exception=True)
        target = state_path()
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            with local_runtime_lock(target):
                data = private_state(target)
                if not data.get("id"):
                    code = form.validated_data.get("code")
                    if not code:
                        raise ValidationError("Сначала привяжите установку по коду из кабинета.")
                    if data.get("code") != code:
                        Command.save_state(target, {
                            "code": code, "connector_key": secrets.token_urlsafe(48),
                        }, exclusive=not target.exists())
                call_command("connect_portal", state=str(target), once=True,
                             prepare_route=form.validated_data["prepare_address"], stdout=StringIO(), stderr=StringIO())
        except SnapshotError:
            raise ValidationError("Подключение уже проверяется. Повторите через несколько секунд.") from None
        except (CommandError, OSError, ValueError, KeyError):
            raise ValidationError("Подключение не завершено. Проверьте код и доступ к owndesk.work, затем повторите.") from None
        return Response({"paired": True, "public_access": False}, headers={"Cache-Control": "no-store"})


def monitor_connection(stop):
    if settings.DEPLOYMENT_MODE != "organization" or not settings.PORTAL_CONNECTOR_STATE_FILE:
        return
    target = state_path()
    while not stop.wait(30):
        try:
            if target.exists():
                with local_runtime_lock(target):
                    if private_state(target).get("id"):
                        call_command("connect_portal", state=str(target), once=True, stdout=StringIO(), stderr=StringIO())
        except (CommandError, SnapshotError, OSError, ValueError, KeyError):
            # A temporary failure leaves the local organization available.
            continue
