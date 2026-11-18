from apps.core.contracts import documented
import hmac
import secrets
from django.conf import settings
from django.db import transaction
from django.shortcuts import get_object_or_404
from rest_framework import serializers
from rest_framework.views import APIView
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.exceptions import ValidationError, NotFound
from apps.core.access import member, require_admin, scoped, tickets_for, team_ids
from apps.teams.models import Team
from apps.conversations.models import Conversation, Message
from apps.tickets.models import Ticket
from apps.tickets.serializers import MessageSerializer
from apps.tickets.services import create_ticket, changed
from apps.core.pagination import Pagination
from .models import ChannelConnection, InboundEvent
from .telegram.adapter import TelegramAdapter, TelegramError, encrypt, secret_hash
from .telegram.inbound import bind


@documented
class ConnectionsView(APIView):
    def get(self, request):
        m = member(request)
        require_admin(m)
        return Response(
            {
                "results": list(
                    ChannelConnection.objects.filter(workspace=m.workspace).values(
                        "id",
                        "username",
                        "bot_id",
                        "state",
                        "last_error",
                        "default_team_id",
                    )
                )
            }
        )

    def post(self, request):
        m = member(request)
        require_admin(m)
        token = str(request.data.get("token", "")).strip()
        team = scoped(Team, m, request.data.get("team_id"))
        local = settings.LOCAL_TELEGRAM_POLLING
        if not local and not settings.PUBLIC_BASE_URL.startswith("https://"):
            raise ValidationError("Настройте PUBLIC_BASE_URL с внешним HTTPS-адресом.")
        try:
            adapter = TelegramAdapter(token=token)
            bot = adapter.call("getMe", {})
            encrypted = encrypt(token)
            if local and adapter.call("getWebhookInfo", {}).get("url"):
                raise ValidationError("Этот бот уже обслуживается через webhook. Для локального запуска выберите отдельного бота.")
        except (TelegramError, ValueError) as e:
            raise ValidationError(str(e)) from None
        if ChannelConnection.objects.filter(bot_id=str(bot["id"])).exists():
            raise ValidationError("Этот бот уже подключён.")
        secret = secrets.token_urlsafe(32)
        connection = ChannelConnection.objects.create(
            workspace=m.workspace,
            bot_id=str(bot["id"]),
            username=bot["username"],
            encrypted_token=encrypted,
            webhook_secret_hash=secret_hash(secret),
            default_team=team,
            transport="polling" if local else "webhook",
        )
        if local:
            connection.state = "connected"
            connection.save(update_fields=["state"])
            return Response({"id": connection.id, "username": connection.username, "state": connection.state}, status=201)
        try:
            adapter.call(
                "setWebhook",
                {
                    "url": f"{settings.PUBLIC_BASE_URL.rstrip('/')}/api/v1/webhooks/telegram/{connection.id}",
                    "secret_token": secret,
                    "allowed_updates": [
                        "message",
                        "edited_message",
                        "callback_query",
                        "my_chat_member",
                    ],
                    "drop_pending_updates": False,
                },
            )
            connection.state = "connected"
        except TelegramError as e:
            connection.state, connection.last_error = "webhook_error", str(e)
        connection.save(update_fields=["state", "last_error"])
        return Response(
            {
                "id": connection.id,
                "username": connection.username,
                "state": connection.state,
                "last_error": connection.last_error,
            },
            status=201,
        )


@documented
class ConnectionHealth(APIView):
    def get(self, request, pk):
        m = member(request)
        require_admin(m)
        c = scoped(ChannelConnection, m, pk)
        result = {
            "id": c.id,
            "state": c.state,
            "pending_events": c.inboundevent_set.filter(state="pending").count(),
        }
        try:
            info = TelegramAdapter(connection=c).call("getWebhookInfo", {})
            result["telegram_pending"] = info.get("pending_update_count", 0)
            result["webhook_configured"] = bool(info.get("url"))
        except TelegramError as e:
            result["error"] = str(e)
        return Response(result)

    def patch(self, request, pk):
        m = member(request)
        require_admin(m)
        connection = scoped(ChannelConnection, m, pk)
        token = str(request.data.get("token", "")).strip()
        try:
            adapter = (
                TelegramAdapter(token=token)
                if token
                else TelegramAdapter(connection=connection)
            )
            if request.data.get("enabled") is False:
                if connection.transport != "polling":
                    adapter.call("deleteWebhook", {"drop_pending_updates": False})
                connection.state = "disabled"
            elif connection.transport == "polling":
                bot = adapter.call("getMe", {})
                if str(bot["id"]) != connection.bot_id or adapter.call("getWebhookInfo", {}).get("url"):
                    raise ValidationError("Проверьте токен бота и отсутствие активного webhook.")
                if token:
                    connection.encrypted_token = encrypt(token)
                connection.username, connection.state = bot["username"], "connected"
            else:
                bot = adapter.call("getMe", {})
                if str(bot["id"]) != connection.bot_id:
                    raise ValidationError("Токен должен принадлежать тому же боту.")
                secret = secrets.token_urlsafe(32)
                adapter.call(
                    "setWebhook",
                    {
                        "url": f"{settings.PUBLIC_BASE_URL.rstrip('/')}/api/v1/webhooks/telegram/{connection.id}",
                        "secret_token": secret,
                        "allowed_updates": [
                            "message",
                            "edited_message",
                            "callback_query",
                            "my_chat_member",
                        ],
                        "drop_pending_updates": False,
                    },
                )
                connection.webhook_secret_hash = secret_hash(secret)
                connection.username, connection.state = bot["username"], "connected"
                if token:
                    connection.encrypted_token = encrypt(token)
            connection.last_error = ""
            connection.save()
        except TelegramError as exc:
            connection.last_error = str(exc)
            connection.save(update_fields=["last_error"])
            raise ValidationError(str(exc)) from None
        from apps.audit.models import AuditEvent

        AuditEvent.objects.create(
            workspace=m.workspace,
            actor=m.user,
            action="connection.updated",
            changes={"id": str(connection.id), "state": connection.state},
        )
        return Response({"id": connection.id, "state": connection.state})


@documented
class WebhookView(APIView):
    permission_classes = [AllowAny]
    authentication_classes = []

    def post(self, request, connection_id):
        connection = get_object_or_404(ChannelConnection, pk=connection_id)
        supplied = request.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
        if not hmac.compare_digest(
            secret_hash(supplied), connection.webhook_secret_hash
        ):
            raise NotFound()
        if "update_id" not in request.data or not isinstance(
            request.data["update_id"], int
        ):
            raise ValidationError("Отсутствует update_id.")
        InboundEvent.objects.get_or_create(
            connection=connection,
            update_id=str(request.data["update_id"]),
            defaults={"payload": request.data},
        )
        return Response({"ok": True})


@documented
class GroupsView(APIView):
    def get(self, request):
        m = member(request)
        require_admin(m)
        return Response(
            {
                "results": list(
                    Conversation.objects.filter(workspace=m.workspace)
                    .exclude(kind__in=["private", "manual"])
                    .values("id", "title", "external_id", "approved", "mode")
                )
            }
        )

    def patch(self, request):
        m = member(request)
        require_admin(m)
        c = scoped(Conversation, m, request.data.get("id"))
        mode = request.data.get("mode", "shared")
        if mode not in ["shared", "single_customer"]:
            raise ValidationError("Неизвестный режим.")
        c.approved, c.mode = bool(request.data.get("approved")), mode
        c.save(update_fields=["approved", "mode"])
        from apps.audit.models import AuditEvent

        AuditEvent.objects.create(
            workspace=m.workspace,
            actor=m.user,
            action="conversation.approved",
            changes={"id": str(c.id), "approved": c.approved, "mode": mode},
        )
        return Response({"ok": True})


def untriaged_for(m):
    from django.db.models import Q

    qs = Message.objects.filter(
        workspace=m.workspace,
        ticket=None,
        direction="inbound",
        conversation__approved=True,
    ).filter(Q(metadata__context_only__isnull=True) | Q(metadata__context_only=False))
    if m.role != "admin":
        qs = qs.filter(conversation__connection__default_team_id__in=team_ids(m))
    return qs


@documented
class UntriagedView(APIView):
    def get(self, request):
        qs = (
            untriaged_for(member(request))
            .select_related("job")
            .prefetch_related("attachments")
            .order_by("-created_at")
        )
        p = Pagination()
        return p.get_paginated_response(
            MessageSerializer(p.paginate_queryset(qs, request), many=True).data
        )


@documented
class TriageView(APIView):
    @transaction.atomic
    def post(self, request, pk):
        m = member(request)
        if m.role not in ["admin", "manager"]:
            from rest_framework.exceptions import PermissionDenied

            raise PermissionDenied()
        message = get_object_or_404(untriaged_for(m).select_for_update(), pk=pk)
        if request.data.get("ticket_id"):
            ticket = get_object_or_404(
                tickets_for(m).select_for_update(),
                pk=request.data["ticket_id"],
                conversation=message.conversation,
                status__in=[
                    "new",
                    "in_progress",
                    "waiting_customer",
                    "waiting_specialist",
                ],
            )
        else:
            ticket = create_ticket(
                m.workspace,
                message.conversation,
                message.conversation.connection.default_team,
                request.data.get("subject") or message.text or "Вложение",
                message.customer,
                m.user,
            )
        bind(message, ticket)
        return Response({"ticket_id": ticket.id})
