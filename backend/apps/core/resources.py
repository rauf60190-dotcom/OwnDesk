from django.db import transaction
from django.utils import timezone
from django.contrib.auth.password_validation import validate_password
from django.contrib.sessions.models import Session
from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework import serializers, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.exceptions import ValidationError, PermissionDenied
from apps.core.access import member, require_admin, tickets_for, team_ids
from apps.accounts.models import User
from apps.workspaces.models import Membership
from apps.teams.models import Team, TeamMember
from apps.customers.models import Customer, Company
from apps.tickets.models import SavedFilter, ReplyTemplate, ACTIVE
from apps.tickets.serializers import TicketSerializer
from apps.tickets.views import hydrated
from apps.tickets.services import changed
from apps.sla.models import SLAPolicy
from apps.audit.models import AuditEvent


def serializer_for(model, fields):
    meta = type(
        "Meta",
        (),
        {"model": model, "fields": fields, "read_only_fields": ["id", "created_at"]},
    )
    return type(
        model.__name__ + "Serializer", (serializers.ModelSerializer,), {"Meta": meta}
    )


class ScopedResource(viewsets.ModelViewSet):
    model = None

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return self.model.objects.none()
        m = member(self.request)
        qs = self.model.objects.filter(workspace=m.workspace).order_by("-created_at")
        if self.model == SavedFilter:
            qs = qs.filter(owner=self.request.user)
        elif self.model == Team and m.role != "admin":
            qs = qs.filter(id__in=team_ids(m))
        elif self.model == Customer and m.role != "admin":
            qs = qs.filter(id__in=tickets_for(m).values("customer_id"))
        elif self.model == Company and m.role != "admin":
            qs = qs.filter(id__in=tickets_for(m).values("customer__company_id"))
        return qs

    def perform_create(self, serializer):
        m = member(self.request)
        if self.model != SavedFilter:
            require_admin(m)
        self.validate_relations(serializer.validated_data, m)
        extra = {"owner": self.request.user} if self.model == SavedFilter else {}
        obj = serializer.save(workspace=m.workspace, **extra)
        AuditEvent.objects.create(
            workspace=m.workspace,
            actor=m.user,
            action=f"{self.model.__name__}.created",
            changes={"id": str(obj.id)},
        )

    def validate_relations(self, data, m):
        for value in data.values():
            if hasattr(value, "workspace_id") and value.workspace_id != m.workspace_id:
                raise ValidationError("Связанный объект недоступен.")

    def perform_update(self, serializer):
        m = member(self.request)
        if self.model != SavedFilter:
            require_admin(m)
        self.validate_relations(serializer.validated_data, m)
        serializer.save()
        AuditEvent.objects.create(
            workspace=m.workspace,
            actor=m.user,
            action=f"{self.model.__name__}.updated",
            changes={"id": str(serializer.instance.id)},
        )

    def perform_destroy(self, instance):
        m = member(self.request)
        if self.model != SavedFilter:
            require_admin(m)
        from django.db.models.deletion import ProtectedError

        try:
            instance.delete()
        except ProtectedError:
            raise ValidationError(
                "Объект используется. Сначала измените связанные записи."
            )


class CustomerViewSet(ScopedResource):
    model = Customer
    serializer_class = serializer_for(
        Customer, ["id", "name", "company", "phone", "language", "extra", "created_at"]
    )

    @action(detail=True, methods=["get"])
    def tickets(self, request, pk=None):
        qs = hydrated(
            tickets_for(member(request)).filter(customer=self.get_object()),
            request.user,
        ).order_by("-updated_at")
        return self.get_paginated_response(
            TicketSerializer(self.paginate_queryset(qs), many=True).data
        )


class CompanyViewSet(ScopedResource):
    model = Company
    serializer_class = serializer_for(Company, ["id", "name", "tax_id", "note"])


class TeamViewSet(ScopedResource):
    model = Team
    serializer_class = serializer_for(Team, ["id", "name"])

    @action(detail=True, methods=["post"])
    def members(self, request, pk=None):
        m = member(request)
        require_admin(m)
        team = self.get_object()
        membership = Membership.objects.filter(
            workspace=m.workspace, pk=request.data.get("membership_id"), active=True
        ).first()
        if not membership:
            raise ValidationError("Сотрудник недоступен.")
        TeamMember.objects.get_or_create(team=team, membership=membership)
        return Response({"ok": True})


class FilterViewSet(ScopedResource):
    model = SavedFilter
    serializer_class = serializer_for(SavedFilter, ["id", "name", "conditions"])


class TemplateViewSet(ScopedResource):
    model = ReplyTemplate
    serializer_class = serializer_for(ReplyTemplate, ["id", "name", "language", "text"])


class PolicySerializer(serializers.ModelSerializer):
    class Meta:
        model = SLAPolicy
        fields = [
            "id",
            "priority",
            "first_response_minutes",
            "next_response_minutes",
            "resolution_minutes",
            "schedule",
        ]

    def validate_schedule(self, value):
        from datetime import time

        if value == {}:
            return value
        if not isinstance(value, dict) or not isinstance(value.get("week"), dict):
            raise serializers.ValidationError("Ожидается объект week с днями 0–6.")
        count = 0
        try:
            if not isinstance(value.get("exceptions", {}), dict):
                raise ValueError()
            def check_spans(spans):
                if not isinstance(spans, list):
                    raise ValueError()
                previous = 0
                for span in spans:
                    if not isinstance(span, (list, tuple)) or len(span) != 2:
                        raise ValueError()
                    start, end = span
                    def seconds(raw):
                        import re
                        if not isinstance(raw, str) or not re.fullmatch(r"\d{2}:\d{2}(?::\d{2})?", raw):
                            raise ValueError()
                        parsed = time.fromisoformat(raw)
                        return parsed.hour * 3600 + parsed.minute * 60 + parsed.second
                    left, right = seconds(start), 86400 if end == "24:00" else seconds(end)
                    if left >= right or left < previous:
                        raise ValueError()
                    previous = right
                return len(spans)
            for day, spans in value["week"].items():
                if str(day) not in list("0123456"):
                    raise ValueError()
                count += check_spans(spans)
            for date, spans in value.get("exceptions", {}).items():
                from datetime import date as dtdate

                dtdate.fromisoformat(date)
                check_spans(spans)
        except (ValueError, TypeError):
            raise serializers.ValidationError(
                "Неверное расписание или пересекающиеся интервалы."
            )
        if not count:
            raise serializers.ValidationError("Укажите хотя бы один рабочий интервал.")
        return value


class PolicyViewSet(ScopedResource):
    model = SLAPolicy
    serializer_class = PolicySerializer

    @action(detail=True, methods=["post"])
    @transaction.atomic
    def recalculate(self, request, pk=None):
        from apps.sla.services import reprioritize

        m = member(request)
        require_admin(m)
        policy = self.get_object()
        if request.data.get("confirm") is not True:
            raise ValidationError("Подтвердите пересчёт активных обращений.")
        count = 0
        for ticket in (
            tickets_for(m)
            .select_for_update()
            .filter(priority=policy.priority, status__in=ACTIVE)
            .iterator()
        ):
            reprioritize(ticket)
            changed(ticket, m.user, "sla.recalculated", {"policy": str(policy.id)})
            count += 1
        return Response({"recalculated": count})


class EmployeeSerializer(serializers.Serializer):
    id = serializers.UUIDField(read_only=True)
    username = serializers.CharField(max_length=150, required=False)
    name = serializers.CharField(max_length=150, required=False)
    password = serializers.CharField(write_only=True, required=False)
    role = serializers.ChoiceField(choices=Membership.ROLES, required=False)
    active = serializers.BooleanField(required=False)
    teams = serializers.ListField(child=serializers.UUIDField(), required=False)


class EmployeeViewSet(viewsets.GenericViewSet):
    serializer_class = EmployeeSerializer

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return Membership.objects.none()
        m = member(self.request)
        staff = Membership.objects.filter(workspace=m.workspace)
        if m.role != "admin":
            from django.db.models import Q

            staff = staff.filter(
                Q(pk=m.pk) | Q(teammember__team_id__in=team_ids(m))
            ).filter(removed_at=None).distinct()
        return staff.select_related("user").prefetch_related("teammember_set")

    def data(self, obj):
        return {
            "id": obj.id,
            "user_id": obj.user_id,
            "username": obj.user.username,
            "name": obj.user.display_name,
            "role": obj.role,
            "is_owner": obj.is_owner,
            "email": obj.user.email,
            "active": obj.active,
            "removed_at": obj.removed_at,
            "teams": [x.team_id for x in obj.teammember_set.all()],
        }

    def list(self, request):
        return Response({"results": [self.data(x) for x in self.get_queryset()]})

    @transaction.atomic
    def create(self, request):
        m = member(request)
        require_admin(m)
        s = self.get_serializer(data=request.data)
        s.is_valid(raise_exception=True)
        d = s.validated_data
        if d.get("role") == "admin" and not m.is_owner:
            raise PermissionDenied("Администраторов добавляет владелец организации.")
        if not d.get("username") or not d.get("password") or not d.get("name"):
            raise ValidationError("Укажите имя, логин и пароль.")
        if User.objects.filter(username=d["username"]).exists():
            raise ValidationError("Логин уже используется.")
        try:
            validate_password(d["password"])
        except DjangoValidationError as e:
            raise ValidationError({"password": e.messages})
        user = User.objects.create_user(
            username=d["username"], password=d["password"], display_name=d["name"]
        )
        obj = Membership.objects.create(
            workspace=m.workspace, user=user, role=d.get("role", "operator")
        )
        self.set_teams(obj, d.get("teams", []))
        AuditEvent.objects.create(
            workspace=m.workspace,
            actor=m.user,
            action="employee.created",
            changes={"id": str(obj.id)},
        )
        return Response(self.data(obj), status=201)

    def set_teams(self, obj, ids):
        teams = list(Team.objects.filter(workspace=obj.workspace, id__in=ids))
        if len(teams) != len(set(ids)):
            raise ValidationError("Недоступная команда.")
        for t in (
            obj.ticket_set.select_for_update()
            .filter(status__in=ACTIVE)
            .exclude(team_id__in=ids)
        ):
            if obj.role != "admin":
                t.assignee = None
                changed(
                    t,
                    self.request.user,
                    "ticket.unassigned",
                    {"reason": "team_access_removed"},
                )
        obj.teammember_set.all().delete()
        TeamMember.objects.bulk_create(
            [TeamMember(team=t, membership=obj) for t in teams]
        )

    @transaction.atomic
    def partial_update(self, request, pk=None):
        m = member(request)
        require_admin(m)
        # Serialize administrative changes to protect the last administrator.
        from apps.workspaces.models import Workspace

        Workspace.objects.select_for_update().get(id=m.workspace_id)
        m = member(request)
        require_admin(m)
        obj = self.get_object()
        if obj.removed_at:
            raise ValidationError("Сначала восстановите доступ сотрудника.")
        if obj.is_owner:
            raise PermissionDenied("Изменение владельца выполняется отдельной передачей владения.")
        s = self.get_serializer(data=request.data, partial=True)
        s.is_valid(raise_exception=True)
        d = s.validated_data
        if obj.role == "admin" and (
            d.get("role", "admin") != "admin" or d.get("active") is False
        ):
            if (
                Membership.objects.filter(
                    workspace=m.workspace, role="admin", active=True
                ).count()
                <= 1
            ):
                raise ValidationError(
                    "Нельзя деактивировать последнего администратора."
                )
        if not m.is_owner and (obj.role == "admin" or d.get("role") == "admin"):
            raise PermissionDenied("Администраторами управляет владелец организации.")
        obj.role, obj.active = d.get("role", obj.role), d.get("active", obj.active)
        if not obj.active or obj.role == "observer":
            for t in obj.ticket_set.select_for_update().filter(status__in=ACTIVE):
                t.assignee = None
                changed(t, m.user, "ticket.unassigned", {"reason": "employee_disabled"})
        obj.save()
        if "name" in d:
            obj.user.display_name = d["name"]
            obj.user.save(update_fields=["display_name"])
        if "teams" in d:
            self.set_teams(obj, d["teams"])
        if not obj.active:
            for session in Session.objects.all().iterator():
                if session.get_decoded().get("_auth_user_id") == str(obj.user_id):
                    session.delete()
        AuditEvent.objects.create(
            workspace=m.workspace,
            actor=m.user,
            action="employee.updated",
            changes={"id": str(obj.id), "active": obj.active, "role": obj.role},
        )
        return Response(self.data(obj))

    @action(detail=True, methods=["post"])
    @transaction.atomic
    def remove(self, request, pk=None):
        return self.change_removal(request, removing=True)

    @action(detail=True, methods=["post"])
    @transaction.atomic
    def restore(self, request, pk=None):
        return self.change_removal(request, removing=False)

    def change_removal(self, request, removing):
        from apps.workspaces.models import Workspace
        m = member(request); require_admin(m)
        Workspace.objects.select_for_update().get(pk=m.workspace_id)
        m = member(request); require_admin(m)
        obj = self.get_object()
        if obj.is_owner or obj.pk == m.pk:
            raise PermissionDenied("Нельзя удалить владельца или собственный доступ.")
        if obj.role == "admin" and not m.is_owner:
            raise PermissionDenied("Администраторами управляет владелец организации.")
        if removing and obj.active and obj.role == "admin" and Membership.objects.filter(
                workspace=m.workspace, role="admin", active=True).count() <= 1:
            raise ValidationError("Нельзя удалить последнего администратора.")
        if bool(obj.removed_at) == removing:
            return Response(self.data(obj))
        obj.removed_at = timezone.now() if removing else None
        # Restoring the record never silently restores login access.
        obj.active = False
        obj.save(update_fields=["active", "removed_at"])
        if removing:
            for ticket in obj.ticket_set.select_for_update().filter(status__in=ACTIVE):
                ticket.assignee = None
                changed(ticket, m.user, "ticket.unassigned", {"reason": "employee_removed"})
            for session in Session.objects.all().iterator():
                if session.get_decoded().get("_auth_user_id") == str(obj.user_id):
                    session.delete()
        AuditEvent.objects.create(workspace=m.workspace, actor=m.user,
            action="employee.removed" if removing else "employee.restored", changes={"id":str(obj.pk)})
        return Response(self.data(obj))
