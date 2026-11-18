from django.db import transaction
from rest_framework import serializers
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.exceptions import PermissionDenied, ValidationError
from drf_spectacular.utils import extend_schema
from apps.core.access import member
from apps.accounts.models import User, SecondFactor, SecurityEvent
from apps.accounts.security import charge, reauthenticate, consume_code, revoke_sessions
from apps.audit.models import AuditEvent
from .models import Workspace, Membership


class TransferInput(serializers.Serializer):
    membership_id = serializers.UUIDField()
    password = serializers.CharField(max_length=256, required=False, write_only=True)
    code = serializers.CharField(max_length=64, required=False, write_only=True)


class TransferOwnership(APIView):
    @extend_schema(request=TransferInput, responses={200: {"type": "object"}})
    def post(self, request):
        current = member(request)
        if not current.is_owner:
            raise PermissionDenied("Передача доступна только владельцу.")
        serializer = TransferInput(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        charge("ownership:" + str(request.user.pk))
        with transaction.atomic():
            Workspace.objects.select_for_update().get(pk=current.workspace_id)
            current = Membership.objects.get(pk=current.pk)
            if not current.is_owner or not current.active:
                raise PermissionDenied("Передача доступна только действующему владельцу.")
            target = Membership.objects.filter(pk=data["membership_id"], workspace_id=current.workspace_id,
                                               active=True, user__is_active=True).first()
            if not target or target.pk == current.pk:
                raise ValidationError("Выберите другого действующего сотрудника организации.")
            request.user = User.objects.select_for_update().get(pk=request.user.pk)
            reauthenticate(request, data)
            factor = SecondFactor.objects.select_for_update().filter(user=request.user, enabled=True).first()
            if factor and not consume_code(factor, data.get("code", "")):
                raise PermissionDenied("Введите действующий код 2FA или резервный код.")
            current.is_owner = False
            current.save(update_fields=["is_owner"])
            target.is_owner, target.role = True, "admin"
            target.save(update_fields=["is_owner", "role"])
            revoke_sessions(target.user)
            AuditEvent.objects.create(workspace_id=current.workspace_id, actor=request.user,
                action="ownership.transferred", changes={"from": str(current.pk), "to": str(target.pk)})
            SecurityEvent.objects.create(user=request.user, kind="ownership_transferred")
            SecurityEvent.objects.create(user=target.user, kind="ownership_received")
        return Response({"ok": True})
