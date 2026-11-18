from django.db import transaction
from django.utils import timezone
from rest_framework.exceptions import ValidationError, PermissionDenied
from apps.core.errors import Conflict
from apps.core.access import tickets_for, require_write, can_manage, team_ids
from apps.workspaces.models import Workspace, Membership
from apps.teams.models import Team, TeamMember
from apps.audit.models import AuditEvent
from apps.conversations.models import Conversation, Message, WEB_SUPPORT
from apps.integrations.models import OutboundJob
from apps.notifications.events import publish, notify
from apps.sla import services as sla
from .models import Ticket, ACTIVE, ESCALATION_LEVELS

TRANSITIONS = {
    "new": ["in_progress"],
    "in_progress": ["waiting_customer", "waiting_specialist", "resolved"],
    "waiting_customer": ["in_progress"],
    "waiting_specialist": ["in_progress"],
    "resolved": ["in_progress", "closed"],
    "closed": [],
}


def audit(ticket, actor, action, changes=None):
    AuditEvent.objects.create(
        workspace=ticket.workspace,
        ticket=ticket,
        actor=actor,
        action=action,
        changes=changes or {},
    )


@transaction.atomic
def create_ticket(
    workspace,
    conversation,
    team,
    subject,
    customer=None,
    actor=None,
    previous=None,
    thread_id=None,
    description="",
    escalation_level="l1",
):
    ws = Workspace.objects.select_for_update().get(id=workspace.id)
    if (
        team.workspace_id != ws.id
        or conversation.workspace_id != ws.id
        or (customer and customer.workspace_id != ws.id)
    ):
        raise ValidationError("Объекты должны принадлежать одному пространству.")
    t = Ticket.objects.create(
        workspace=ws,
        conversation=conversation,
        team=team,
        subject=subject[:240],
        description=description,
        escalation_level=escalation_level,
        number=ws.next_ticket_number,
        customer=customer,
        previous_ticket=previous,
        thread_id=thread_id,
    )
    ws.next_ticket_number += 1
    ws.save(update_fields=["next_ticket_number"])
    audit(t, actor, "ticket.created")
    publish(t, "ticket.created")
    return t


def locked(m, pk, expected):
    from django.shortcuts import get_object_or_404

    t = get_object_or_404(tickets_for(m).select_for_update(), pk=pk)
    if expected is None or t.version != expected:
        raise Conflict(
            current={
                "version": t.version,
                "assignee_id": str(t.assignee_id) if t.assignee_id else None,
                "assignee_name": t.assignee.user.display_name
                if t.assignee_id
                else None,
            }
        )
    return t


def changed(t, actor, action, changes):
    t.version += 1
    t.save()
    audit(t, actor, action, changes)
    publish(t)
    return t


@transaction.atomic
def claim(m, pk, expected):
    t = locked(m, pk, expected)
    if m.role == "observer":
        raise PermissionDenied()
    if t.assignee_id or t.status not in ACTIVE:
        raise Conflict("Обращение уже назначено или завершено.")
    t.assignee = m
    if t.status == "new":
        t.status = "in_progress"
    changed(t, m.user, "ticket.claimed", {"assignee": str(m.id)})
    return t


@transaction.atomic
def assign(m, pk, expected, team_id, assignee_id):
    t = locked(m, pk, expected)
    if not can_manage(m, t):
        raise PermissionDenied()
    team = Team.objects.filter(pk=team_id, workspace=m.workspace).first()
    if not team or (m.role != "admin" and team.id not in team_ids(m)):
        raise ValidationError("Недоступная команда.")
    assignee = None
    if assignee_id:
        assignee = (
            Membership.objects.filter(
                pk=assignee_id, workspace=m.workspace, active=True, user__is_active=True
            )
            .exclude(role="observer")
            .first()
        )
        if not assignee or (
            assignee.role != "admin"
            and not TeamMember.objects.filter(team=team, membership=assignee).exists()
        ):
            raise ValidationError("Сотрудник не имеет доступа к команде.")
    if t.escalation_level != "l1" and t.team_id != team.id:
        raise ValidationError("Команду эскалированного обращения меняют через маршрут передачи.")
    t.team, t.assignee = team, assignee
    changed(
        t,
        m.user,
        "ticket.assigned",
        {"team": str(team.id), "assignee": str(assignee.id) if assignee else None},
    )
    notify(t, "assigned", f"Вам назначено обращение №{t.number}")
    return t


@transaction.atomic
def escalate(m, pk, expected, reason):
    t = locked(m, pk, expected)
    require_write(m, t)
    if t.status not in ACTIVE:
        raise ValidationError("Передавать можно только открытые обращения.")
    index = ESCALATION_LEVELS.index(t.escalation_level)
    if index == len(ESCALATION_LEVELS) - 1:
        raise ValidationError("Обращение уже у разработчиков.")
    level = ESCALATION_LEVELS[index + 1]
    route = Workspace.objects.get(pk=m.workspace_id).settings.get("escalation_route", {})
    team_id = route.get(level)
    team = Team.objects.filter(pk=team_id, workspace=m.workspace).first() if team_id else None
    if not team:
        raise ValidationError("Администратору нужно настроить команду следующей линии.")
    if team.pk == t.team_id:
        raise ValidationError("Следующая линия должна быть другой командой.")
    changes = {
        "from": t.escalation_level, "to": level,
        "from_team": t.team.name, "to_team": team.name, "reason": reason,
    }
    t.escalation_level, t.team, t.assignee = level, team, None
    return changed(t, m.user, "ticket.escalated", changes)


@transaction.atomic
def transition(m, pk, expected, status, resolution=""):
    t = locked(m, pk, expected)
    require_write(m, t)
    if status not in TRANSITIONS[t.status]:
        raise ValidationError("Недопустимый переход статуса.")
    if status == "resolved" and not resolution.strip():
        raise ValidationError({"resolution": "Укажите краткий результат."})
    old = t.status
    t.status = status
    if status == "resolved":
        t.resolution, t.resolved_at = resolution.strip(), timezone.now()
    elif status == "closed":
        t.closed_at = timezone.now()
    elif old == "resolved":
        t.resolved_at = None
        sla.incoming(t, timezone.now(), restart=True)
    sla.status_changed(t, old)
    return changed(
        t,
        m.user,
        "ticket.status",
        {"from": old, "to": status, "resolution": resolution},
    )


@transaction.atomic
def edit(m, pk, expected, values):
    t = locked(m, pk, expected)
    require_write(m, t)
    changes = {}
    for field in ["subject", "description", "priority"]:
        if field in values and getattr(t, field) != values[field]:
            changes[field] = {"from": getattr(t, field), "to": values[field]}
            setattr(t, field, values[field])
    if "priority" in changes:
        sla.reprioritize(t)
    return changed(t, m.user, "ticket.updated", changes)


@transaction.atomic
def send(
    m, pk, expected, text, operation_key, note=False, reply_id=None, attachment_ids=None
):
    # Check idempotency under the same ticket lock before rejecting a stale version.
    from django.shortcuts import get_object_or_404

    t = get_object_or_404(tickets_for(m).select_for_update(), pk=pk)
    require_write(m, t, note=note)
    previous = Message.objects.filter(
        workspace=m.workspace, operation_key=operation_key
    ).first()
    if previous:
        if previous.ticket_id != t.id or previous.author_id != m.user_id:
            raise Conflict("Ключ операции уже использован.")
        if (
            previous.text != text
            or previous.reply_id != reply_id
            or (previous.visibility == "internal") != note
            or {str(pk) for pk in previous.attachments.values_list("pk", flat=True)}
            != {str(pk) for pk in attachment_ids or []}
        ):
            raise Conflict("Ключ операции относится к другому содержимому сообщения.")
        return previous
    if expected != t.version:
        raise Conflict()
    if t.status not in ACTIVE:
        raise ValidationError("Сначала откройте обращение повторно.")
    web_support = t.conversation.kind == WEB_SUPPORT
    if not note and not web_support and not t.conversation.connection_id:
        raise ValidationError(
            "К этому обращению не подключён Telegram. Доступны внутренние заметки."
        )
    if not note and not t.conversation.approved:
        raise ValidationError("Чат ещё не подтверждён администратором.")
    if not note and not web_support and t.conversation.connection.state != "connected":
        raise ValidationError(
            "Подключение Telegram недоступно. Проверьте его состояние в настройках."
        )
    if not text.strip() and not attachment_ids:
        raise ValidationError("Введите сообщение или приложите файл.")
    if len(text) > 20000:
        raise ValidationError("Максимум 20 000 символов в сообщении.")
    reply = None
    if reply_id:
        reply = Message.objects.filter(
            ticket=t, external_id=reply_id, visibility="public"
        ).first()
        if reply is None:
            raise ValidationError("Сообщение для ответа недоступно.")
    from apps.attachments.models import Attachment

    files = list(
        Attachment.objects.select_for_update().filter(
            id__in=attachment_ids or [],
            workspace=m.workspace,
            uploaded_by=m.user,
            message=None,
            state="ready",
        )
    )
    if len(files) != len(attachment_ids or []):
        raise ValidationError("Вложение недоступно.")
    if len(files) > 1:
        raise ValidationError("За одну отправку можно приложить один файл.")
    message = Message.objects.create(
        workspace=m.workspace,
        ticket=t,
        conversation=t.conversation,
        author=m.user,
        author_name=m.user.display_name.strip() or "Сотрудник поддержки",
        direction="outbound",
        visibility="internal" if note else "public",
        kind="note" if note else "text",
        text=text,
        operation_key=operation_key,
        reply_id=reply_id,
        thread_id=reply.thread_id if reply else t.thread_id,
    )
    for a in files:
        a.message = message
        a.save(update_fields=["message"])
    action = "note.created" if note else "message.queued"
    if not note and web_support:
        message.sent_at = message.created_at
        message.save(update_fields=["sent_at"])
        t.last_agent_message_at = message.created_at
        sla.confirmed_response(t, t.version, message.created_at)
        action = "support.reply"
    elif not note:
        OutboundJob.objects.create(message=message)
    changed(
        t,
        m.user,
        action,
        {"message": str(message.id)},
    )
    return message
