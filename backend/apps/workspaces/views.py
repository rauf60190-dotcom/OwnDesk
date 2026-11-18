from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from django.db import transaction
from rest_framework import serializers
from rest_framework.generics import GenericAPIView
from rest_framework.response import Response
from drf_spectacular.utils import extend_schema
from apps.core.access import member, require_admin
from apps.audit.models import AuditEvent
from .models import Workspace


class WorkspaceSettings(serializers.Serializer):
    timezone = serializers.CharField(max_length=100, required=False)
    reopen_hours = serializers.IntegerField(min_value=0, max_value=720, required=False)
    event_retention_days = serializers.IntegerField(
        min_value=0, max_value=36500, required=False
    )
    file_retention_days = serializers.IntegerField(
        min_value=0, max_value=36500, required=False
    )

    def validate_timezone(self, name):
        try:
            ZoneInfo(name)
        except (ValueError, ZoneInfoNotFoundError):
            raise serializers.ValidationError("Укажите действующий часовой пояс IANA.")
        return name


class WorkspaceSettingsView(GenericAPIView):
    serializer_class = WorkspaceSettings

    def result(self, workspace):
        return {
            "timezone": workspace.timezone,
            "reopen_hours": workspace.settings.get("reopen_hours", 24),
            "event_retention_days": workspace.settings.get("event_retention_days", 0),
            "file_retention_days": workspace.settings.get("file_retention_days", 0),
        }

    def get(self, request):
        m = member(request)
        require_admin(m)
        return Response(self.result(m.workspace))

    @transaction.atomic
    def patch(self, request):
        m = member(request)
        require_admin(m)
        ws = Workspace.objects.select_for_update().get(id=m.workspace_id)
        previous = self.result(ws)
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        values = dict(serializer.validated_data)
        ws.timezone = values.pop("timezone", ws.timezone)
        ws.settings = {**ws.settings, **values}
        ws.save(update_fields=["timezone", "settings"])
        AuditEvent.objects.create(
            workspace=ws,
            actor=m.user,
            action="workspace.settings",
            changes={"from": previous, "to": self.result(ws)},
        )
        return Response(self.result(ws))

class EscalationRouteInput(serializers.Serializer):
    l2 = serializers.UUIDField(allow_null=True)
    l3 = serializers.UUIDField(allow_null=True)
    development = serializers.UUIDField(allow_null=True)


class EscalationRouteView(GenericAPIView):
    serializer_class = EscalationRouteInput

    def result(self, workspace):
        from apps.teams.models import Team

        route = workspace.settings.get("escalation_route", {})
        teams = {str(team.pk): {"id": team.pk, "name": team.name} for team in
                 Team.objects.filter(workspace=workspace, pk__in=[key for key in route.values() if key])}
        return {level: teams.get(route.get(level)) for level in ("l2", "l3", "development")}

    @extend_schema(responses={200: {"type": "object"}})
    def get(self, request):
        return Response(self.result(member(request).workspace))

    @extend_schema(responses={200: {"type": "object"}})
    @transaction.atomic
    def patch(self, request):
        from apps.teams.models import Team

        m = member(request)
        require_admin(m)
        form = self.get_serializer(data=request.data)
        form.is_valid(raise_exception=True)
        values = form.validated_data
        team_ids = [team_id for team_id in values.values() if team_id is not None]
        if len(set(team_ids)) != len(team_ids):
            raise serializers.ValidationError("Выберите отдельную команду для каждой линии.")
        if Team.objects.filter(workspace=m.workspace, pk__in=team_ids).count() != len(team_ids):
            raise serializers.ValidationError("Команда недоступна в вашей организации.")
        workspace = Workspace.objects.select_for_update().get(pk=m.workspace_id)
        before = workspace.settings.get("escalation_route", {})
        route = {level: str(team_id) if team_id else None for level, team_id in values.items()}
        workspace.settings = {**workspace.settings, "escalation_route": route}
        workspace.save(update_fields=["settings"])
        AuditEvent.objects.create(workspace=workspace, actor=m.user, action="workspace.escalation_route",
                                  changes={"from": before, "to": route})
        return Response(self.result(workspace))
