"""Customer support on an organization installation. The link is a bearer credential."""
import hashlib
import secrets
import uuid
from datetime import timedelta
from pathlib import PurePath

from django.conf import settings
from django.db import transaction
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.utils.decorators import method_decorator
from django.views.decorators.cache import never_cache
from django.views.decorators.csrf import csrf_protect
from drf_spectacular.utils import extend_schema
from rest_framework import serializers
from rest_framework.exceptions import NotFound, PermissionDenied, ValidationError
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.registration import digest, public_origin
from apps.accounts.security import charge
from apps.attachments.models import Attachment
from apps.attachments.storage import detect_type, storage
from apps.attachments.views import attachment_response
from apps.audit.models import AuditEvent
from apps.conversations.models import Conversation, Message, WEB_SUPPORT
from apps.core.access import member, require_admin, scoped
from apps.core.pagination import Pagination
from apps.notifications.events import notify
from apps.sla import services as sla
from apps.teams.models import Team
from apps.tickets import services
from apps.tickets.models import ACTIVE, Ticket
from apps.tickets.serializers import AttachmentSerializer
from .models import Customer, SupportAccess

SESSION_KEY = "customer_support"


class SupportIssueLinkInput(serializers.Serializer):
    team_id = serializers.UUIDField()


class SupportExchangeInput(serializers.Serializer):
    token = serializers.RegexField(r"^[A-Za-z0-9_-]{43}$", write_only=True)


class SupportMessageInput(serializers.Serializer):
    text = serializers.CharField(max_length=20000, required=False, allow_blank=True, default="")
    file = serializers.FileField(required=False, write_only=True)
    operation_key = serializers.UUIDField()

    def validate_file(self, value):
        if value.size > settings.MAX_UPLOAD_BYTES:
            raise serializers.ValidationError("Файл должен быть не больше 20 МБ.")
        return value

    def validate(self, values):
        if not values["text"] and "file" not in values:
            raise serializers.ValidationError("Введите сообщение или приложите файл.")
        return values


class SupportCreateInput(SupportMessageInput):
    subject = serializers.CharField(max_length=240)


@method_decorator(never_cache, name="dispatch")
class CustomerSupportLink(APIView):
    @extend_schema(request=SupportIssueLinkInput, responses={201: {"type": "object"}})
    @transaction.atomic
    def post(self, request, customer_id):
        staff = member(request)
        require_admin(staff)
        customer = scoped(Customer, staff, customer_id)
        origin = public_origin()
        if not origin:
            raise ValidationError("Настройте публичный адрес сервера организации.")
        form = SupportIssueLinkInput(data=request.data)
        form.is_valid(raise_exception=True)
        team = scoped(Team, staff, form.validated_data["team_id"])
        token = secrets.token_urlsafe(32)
        access, _ = SupportAccess.objects.update_or_create(customer=customer, defaults={
            "workspace": staff.workspace, "team": team, "token_hash": digest(token),
            "expires_at": timezone.now() + timedelta(days=7),
        })
        AuditEvent.objects.create(workspace=staff.workspace, actor=staff.user,
            action="support.access_issued", changes={"customer_id": str(customer.pk)})
        return Response({"link": f"{origin}/support#access={token}", "expires_at": access.expires_at}, status=201)

    @extend_schema(request=None, responses={204: None})
    @transaction.atomic
    def delete(self, request, customer_id):
        staff = member(request)
        require_admin(staff)
        customer = scoped(Customer, staff, customer_id)
        deleted, _ = SupportAccess.objects.filter(customer=customer, workspace=staff.workspace).delete()
        if deleted:
            AuditEvent.objects.create(workspace=staff.workspace, actor=staff.user,
                action="support.access_revoked", changes={"customer_id": str(customer.pk)})
        return Response(status=204)


def valid_access(token_hash, lock=False):
    query = SupportAccess.objects.select_related("customer", "team", "workspace")
    if lock:
        query = query.select_for_update(of=("self",))
    grant = query.filter(token_hash=token_hash, expires_at__gt=timezone.now()).first()
    if not grant or grant.customer.workspace_id != grant.workspace_id or grant.team.workspace_id != grant.workspace_id:
        raise PermissionDenied("Ссылка недействительна. Запросите новую ссылку у поддержки.")
    return grant


def visible_tickets(grant):
    return Ticket.objects.filter(workspace=grant.workspace, customer=grant.customer,
        conversation__kind=WEB_SUPPORT, conversation__workspace=grant.workspace)


def summary(ticket):
    return {key: getattr(ticket, key) for key in ("id", "number", "subject", "status", "updated_at")}


def public_message(message):
    return {
        "id": message.pk, "text": message.text, "created_at": message.created_at,
        "direction": message.direction,
        "attachments": AttachmentSerializer([item for item in message.attachments.all() if item.state == "ready"], many=True).data,
    }


def paginate(request, queryset, serialize, extra=None):
    pagination = Pagination()
    rows = pagination.paginate_queryset(queryset, request)
    response = pagination.get_paginated_response([serialize(row) for row in rows])
    if extra:
        response.data.update(extra)
    return response


def fingerprint(file):
    if file is None:
        return None
    digest = hashlib.file_digest(file.file, "sha256").hexdigest()
    file.seek(0)
    return [file.name, file.size, digest]


def append_message(grant, ticket, data):
    previous_status = ticket.status
    if previous_status == "waiting_customer":
        ticket.status = "in_progress" if ticket.assignee_id else "new"
        sla.status_changed(ticket, previous_status)
    message = Message.objects.create(
        workspace=grant.workspace, customer=grant.customer, conversation=ticket.conversation,
        ticket=ticket, direction="inbound", visibility="public", author_name=grant.customer.name,
        text=data["text"], operation_key=data["operation_key"], ticket_sequence=ticket.version + 1,
        metadata={"support_file": fingerprint(data.get("file"))},
    )
    ticket.last_customer_message_at = message.created_at
    sla.incoming(ticket, message.created_at, sequence=message.ticket_sequence)
    services.changed(ticket, None, "message.received", {"message": str(message.pk), "source": WEB_SUPPORT})
    notify(ticket, "customer_message", f"Новое сообщение в обращении №{ticket.number}")
    if (file := data.get("file")) is not None:
        blob = file.read()
        mime = detect_type(blob)
        key = f"{grant.workspace_id}/{uuid.uuid4()}"
        Attachment.objects.create(workspace=grant.workspace, message=message, object_key=key,
            filename=PurePath(file.name).name[:240], size=len(blob), mime_type=mime, state="ready")
        try:
            storage().put_object(Bucket=settings.S3_BUCKET, Key=key, Body=blob, ContentType=mime)
        except Exception:
            raise ValidationError("Хранилище недоступно. Сообщение не сохранено.") from None
    return message


def previous_message(grant, data, ticket_id=None):
    message = Message.objects.filter(workspace=grant.workspace, operation_key=data["operation_key"]).first()
    if message and (
        not visible_tickets(grant).filter(pk=message.ticket_id).exists()
        or message.direction != "inbound" or message.customer_id != grant.customer_id
        or message.text != data["text"]
        or message.metadata.get("support_file") != fingerprint(data.get("file"))
        or (ticket_id is not None and ticket_id != message.ticket_id)
        or (ticket_id is None and message.ticket.subject != data["subject"])
    ):
        raise ValidationError("Этот ключ операции уже использован.")
    return message


@method_decorator([csrf_protect, never_cache], name="dispatch")
class SupportView(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]

    def initial(self, request, *args, **kwargs):
        if settings.DEPLOYMENT_MODE == "portal":
            raise NotFound()
        super().initial(request, *args, **kwargs)

    def grant(self, request, lock=False):
        return valid_access(request.session.get(SESSION_KEY, ""), lock=lock)


class SupportSession(SupportView):
    @extend_schema(request=SupportExchangeInput, responses={200: {"type": "object"}})
    def post(self, request):
        form = SupportExchangeInput(data=request.data)
        form.is_valid(raise_exception=True)
        token_hash = digest(form.validated_data["token"])
        grant = valid_access(token_hash)
        if request.session.get(SESSION_KEY) != token_hash:
            charge(f"support-session:{grant.pk}", limit=30)
            request.session.cycle_key()
            request.session[SESSION_KEY] = token_hash
        return Response({"ok": True})

    @extend_schema(request=None, responses={204: None})
    def delete(self, request):
        request.session.pop(SESSION_KEY, None)
        return Response(status=204)


class SupportTickets(SupportView):
    @extend_schema(responses={200: {"type": "object"}})
    def get(self, request):
        grant = self.grant(request)
        return paginate(request, visible_tickets(grant).order_by("-updated_at", "-id"), summary,
            {"customer": grant.customer.name, "organization": grant.workspace.name})

    @extend_schema(request=SupportCreateInput, responses={201: {"type": "object"}})
    def post(self, request):
        grant = self.grant(request)
        charge(f"support-create:{grant.pk}", limit=10)
        form = SupportCreateInput(data=request.data)
        form.is_valid(raise_exception=True)
        data = form.validated_data
        with transaction.atomic():
            grant = self.grant(request, lock=True)
            if previous := previous_message(grant, data):
                return Response(summary(previous.ticket), status=201)
            conversation = Conversation.objects.create(workspace=grant.workspace, kind=WEB_SUPPORT,
                approved=True, title=grant.customer.name)
            ticket = services.create_ticket(grant.workspace, conversation, grant.team, data["subject"], grant.customer)
            append_message(grant, ticket, data)
        return Response(summary(ticket), status=201)


class SupportMessages(SupportView):
    @extend_schema(responses={200: {"type": "object"}})
    def get(self, request, ticket_id):
        grant = self.grant(request)
        ticket = get_object_or_404(visible_tickets(grant), pk=ticket_id)
        messages = ticket.messages.filter(visibility="public").prefetch_related("attachments").order_by("-created_at", "-id")
        return paginate(request, messages, public_message, {"ticket": summary(ticket)})

    @extend_schema(request=SupportMessageInput, responses={201: {"type": "object"}})
    def post(self, request, ticket_id):
        grant = self.grant(request)
        charge(f"support-message:{grant.pk}", limit=40)
        form = SupportMessageInput(data=request.data)
        form.is_valid(raise_exception=True)
        data = form.validated_data
        with transaction.atomic():
            grant = self.grant(request, lock=True)
            ticket = get_object_or_404(visible_tickets(grant).select_for_update(of=("self",)), pk=ticket_id)
            if previous := previous_message(grant, data, ticket_id):
                return Response(public_message(previous), status=201)
            if ticket.status not in ACTIVE:
                raise ValidationError("Обращение завершено. Для нового вопроса создайте обращение.")
            message = append_message(grant, ticket, data)
        return Response(public_message(message), status=201)


class SupportDownload(SupportView):
    @extend_schema(responses={(200, "application/octet-stream"): {"type": "string", "format": "binary"}})
    def get(self, request, attachment_id):
        grant = self.grant(request)
        file = get_object_or_404(Attachment.objects.filter(
            workspace=grant.workspace, state="ready", message__visibility="public",
            message__ticket_id__in=visible_tickets(grant).values("id")), pk=attachment_id)
        return attachment_response(file, request.query_params.get("preview") == "1")
