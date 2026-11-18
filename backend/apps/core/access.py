from django.db.models import Q
from rest_framework.exceptions import PermissionDenied, NotFound
from apps.workspaces.models import Membership
from apps.teams.models import TeamMember
from apps.tickets.models import Ticket


def member(request):
    m = (
        Membership.objects.select_related("workspace", "user")
        .filter(
            user=request.user,
            active=True,
            workspace_id=request.session.get("workspace_id"),
        )
        .first()
    )
    if not m:
        raise PermissionDenied("Нет доступа к рабочему пространству.")
    return m


def team_ids(m):
    return TeamMember.objects.filter(membership=m).values_list("team_id", flat=True)


def tickets_for(m):
    qs = Ticket.objects.filter(workspace=m.workspace)
    if m.role != "admin":
        qs = qs.filter(team_id__in=team_ids(m))
    return qs


def can_manage(m, ticket):
    return m.role == "admin" or (m.role == "manager" and ticket.team_id in team_ids(m))


def require_write(m, ticket, note=False):
    if m.role == "observer" or (
        not note and not can_manage(m, ticket) and ticket.assignee_id != m.id
    ):
        raise PermissionDenied(
            "Действие доступно ответственному или руководителю команды."
        )


def require_admin(m):
    if m.role != "admin":
        raise PermissionDenied("Действие доступно администратору.")


def scoped(model, m, pk):
    obj = model.objects.filter(pk=pk, workspace=m.workspace).first()
    if not obj:
        raise NotFound()
    return obj
