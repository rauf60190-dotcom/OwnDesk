from datetime import datetime, timezone as dtzone, timedelta
from django.db import transaction
from django.utils import timezone
from apps.conversations.models import Conversation, ConversationParticipant, Message
from apps.customers.models import Customer, CustomerIdentity
from apps.integrations.models import InboundEvent, OutboundJob
from apps.attachments.models import Attachment
from apps.tickets.models import Ticket, ACTIVE
from apps.tickets.services import create_ticket, changed, audit
from apps.notifications.events import publish, notify
from apps.sla import services as sla


def automatic(conv, text, markup=None):
    msg = Message.objects.create(
        workspace=conv.workspace,
        conversation=conv,
        direction="automatic",
        author_name="Бот",
        text=text,
        metadata={"reply_markup": markup} if markup else {},
    )
    OutboundJob.objects.create(message=msg)


def bind(message, ticket):
    message.ticket = ticket
    message.ticket_sequence = ticket.version + 1
    message.save(update_fields=["ticket", "ticket_sequence"])
    old = ticket.status
    if old == "waiting_customer":
        ticket.status = "in_progress" if ticket.assignee_id else "new"
        sla.status_changed(ticket, old)
    elif old == "resolved":
        ticket.status = "in_progress" if ticket.assignee_id else "new"
        ticket.resolved_at = None
        audit(ticket, None, "ticket.reopened")
    ticket.last_customer_message_at = max(
        ticket.last_customer_message_at or message.created_at, message.created_at
    )
    sla.incoming(
        ticket,
        message.created_at,
        sequence=message.ticket_sequence,
        restart=old == "resolved",
    )
    changed(ticket, None, "message.received", {"message": str(message.id)})
    notify(ticket, "customer_message", f"Новое сообщение в обращении №{ticket.number}")


def choose_callback(connection, callback):
    data = callback.get("data", "").split(":")
    if len(data) not in [2, 3] or data[0] not in ["new", "pick"]:
        return
    from uuid import UUID

    try:
        UUID(data[1])
    except ValueError:
        return
    pending = (
        Message.objects.select_for_update()
        .filter(
            pk=data[1],
            conversation__connection=connection,
            ticket=None,
            metadata__sender_id=str(callback.get("from", {}).get("id")),
            conversation__external_id=str(
                callback.get("message", {}).get("chat", {}).get("id")
            ),
        )
        .first()
    )
    if not pending or pending.conversation.kind != "private":
        return
    if data[0] == "pick":
        if len(data) != 3 or not data[2].isdigit():
            return
        ticket = (
            Ticket.objects.select_for_update()
            .filter(
                conversation=pending.conversation,
                number=int(data[2]),
                status__in=ACTIVE,
            )
            .first()
        )
        if not ticket:
            return
    else:
        ticket = create_ticket(
            connection.workspace,
            pending.conversation,
            connection.default_team,
            pending.text or "Вложение",
            pending.customer,
        )
    bind(pending, ticket)


@transaction.atomic
def process_event(event_id):
    event = (
        InboundEvent.objects.select_for_update()
        .select_related("connection__workspace", "connection__default_team")
        .get(pk=event_id)
    )
    if event.state == "done":
        return
    connection = event.connection
    if connection.state == "disabled":
        return
    payload = event.payload
    if "callback_query" in payload:
        choose_callback(connection, payload["callback_query"])
        event.state = "done"
        event.save(update_fields=["state"])
        return
    raw = payload.get("message") or payload.get("edited_message")
    if not raw or not raw.get("chat"):
        event.state = "ignored"
        event.save(update_fields=["state"])
        return
    chat = raw["chat"]
    sender = raw.get("from", {})
    sender_id = str(sender.get("id", ""))
    conv, _ = Conversation.objects.get_or_create(
        connection=connection,
        external_id=str(chat["id"]),
        defaults={
            "workspace": connection.workspace,
            "kind": chat["type"],
            "approved": chat["type"] == "private",
            "title": chat.get("title")
            or " ".join(
                filter(None, [sender.get("first_name"), sender.get("last_name")])
            )
            or "Telegram",
            "mode": "single_customer" if chat["type"] == "private" else "shared",
        },
    )
    conv = Conversation.objects.select_for_update().get(id=conv.id)
    name = " ".join(
        filter(None, [sender.get("first_name"), sender.get("last_name")])
    ) or chat.get("title", "Клиент")
    customer = None
    if sender_id:
        identity = CustomerIdentity.objects.filter(
            workspace=connection.workspace, external_id=sender_id
        ).first()
        if identity:
            customer = identity.customer
        else:
            # Workspace row serializes identity creation across different chats of the same person.
            from apps.workspaces.models import Workspace

            Workspace.objects.select_for_update().get(id=connection.workspace_id)
            identity = CustomerIdentity.objects.filter(
                workspace=connection.workspace, external_id=sender_id
            ).first()
            if identity:
                customer = identity.customer
            else:
                customer = Customer.objects.create(
                    workspace=connection.workspace,
                    name=name,
                    language=sender.get("language_code", "ru")[:10],
                )
                CustomerIdentity.objects.create(
                    workspace=connection.workspace,
                    customer=customer,
                    external_id=sender_id,
                )
    if sender_id:
        ConversationParticipant.objects.update_or_create(
            workspace=connection.workspace,
            conversation=conv,
            external_id=sender_id,
            defaults={"customer": customer},
        )
    contact = raw.get("contact") or {}
    if (
        customer
        and str(contact.get("user_id", "")) == sender_id
        and contact.get("phone_number")
    ):
        customer.phone = contact["phone_number"][:40]
        customer.save(update_fields=["phone"])
    text = raw.get("text") or raw.get("caption", "")
    command_token = text.split(" ", 1)[0].lower().split("@", 1)
    command = (
        command_token[0]
        if len(command_token) == 1 or command_token[1] == connection.username.lower()
        else ""
    )

    def record_command():
        _, is_new = Message.objects.get_or_create(
            conversation=conv,
            external_id=str(raw["message_id"]),
            defaults={
                "workspace": connection.workspace,
                "direction": "inbound",
                "kind": "command",
                "customer": customer,
                "author_name": name,
                "text": text,
                "metadata": {"sender_id": sender_id, "context_only": True},
            },
        )
        return is_new

    if conv.approved and conv.kind == "private" and command in ["/start", "/help"]:
        if record_command():
            automatic(
                conv,
                "Здравствуйте! Опишите проблему — мы создадим обращение. Для новой проблемы используйте /new и описание.",
                {
                    "keyboard": [[{"text": "Создать обращение"}]],
                    "resize_keyboard": True,
                },
            )
        event.state = "done"
        event.save(update_fields=["state"])
        return
    if (
        conv.approved
        and conv.kind == "private"
        and text in ["Создать обращение", "Новая проблема"]
    ):
        if record_command():
            automatic(conv, "Напишите /new и описание новой проблемы.")
        event.state = "done"
        event.save(update_fields=["state"])
        return
    existing = (
        Message.objects.select_for_update()
        .filter(conversation=conv, external_id=str(raw["message_id"]))
        .first()
    )
    external_date = datetime.fromtimestamp(raw.get("date", 0), dtzone.utc)
    edited_at = (
        datetime.fromtimestamp(raw["edit_date"], dtzone.utc)
        if raw.get("edit_date")
        else None
    )
    if existing:
        if edited_at and (not existing.edited_at or edited_at > existing.edited_at):
            existing.text, existing.edited_at = text, edited_at
            existing.save(update_fields=["text", "edited_at"])
            if existing.ticket_id:
                changed(
                    existing.ticket,
                    None,
                    "message.edited",
                    {"message": str(existing.id)},
                )
        event.state = "done"
        event.save(update_fields=["state"])
        return
    kind, media = "text", None
    for media_kind in ["photo", "document", "video", "voice", "audio"]:
        if raw.get(media_kind):
            kind = media_kind
            media = raw[media_kind][-1] if media_kind == "photo" else raw[media_kind]
            break
    if not text and not media:
        kind, text = "unsupported", "Неподдерживаемый тип сообщения"
    message = Message.objects.create(
        workspace=connection.workspace,
        conversation=conv,
        customer=customer,
        author_name=name,
        text=text,
        kind=kind,
        external_id=str(raw["message_id"]),
        external_date=external_date,
        edited_at=edited_at,
        reply_id=str(raw["reply_to_message"]["message_id"])
        if raw.get("reply_to_message")
        else None,
        thread_id=str(raw["message_thread_id"])
        if raw.get("message_thread_id")
        else None,
        metadata={"sender_id": sender_id},
    )
    # Worker downtime must not reset the SLA. Use durable webhook acceptance time.
    Message.objects.filter(pk=message.pk).update(created_at=event.created_at)
    message.created_at = event.created_at
    if media:
        Attachment.objects.create(
            workspace=connection.workspace,
            message=message,
            filename=media.get("file_name") or f"{kind}-{raw['message_id']}",
            size=media.get("file_size", 0),
            telegram_file_id=media["file_id"],
            state="pending",
        )
    if conv.approved:
        active = Ticket.objects.select_for_update().filter(
            conversation=conv, status__in=ACTIVE
        )
        ticket = None
        if message.reply_id:
            target = Message.objects.filter(
                conversation=conv, external_id=message.reply_id, ticket__isnull=False
            ).first()
            if target:
                ticket = Ticket.objects.select_for_update().get(id=target.ticket_id)
        if not ticket and message.thread_id:
            candidates = active.filter(thread_id=message.thread_id)
            if candidates.count() == 1:
                ticket = candidates.first()
        force_new = command == "/new"
        mentioned = f"@{connection.username}".lower() in text.lower()
        if (
            not ticket
            and not force_new
            and (conv.kind == "private" or conv.mode == "single_customer")
        ):
            if active.count() == 1:
                ticket = active.first()
            elif not active.exists():
                ticket = (
                    Ticket.objects.select_for_update()
                    .filter(conversation=conv, status__in=["resolved", "closed"])
                    .order_by("-updated_at")
                    .first()
                )
        previous = None
        if ticket and (
            ticket.status == "closed"
            or (
                ticket.status == "resolved"
                and (
                    not ticket.resolved_at
                    or timezone.now() - ticket.resolved_at
                    > timedelta(
                        hours=connection.workspace.settings.get("reopen_hours", 24)
                    )
                )
            )
        ):
            previous, ticket = ticket, None
        if force_new:
            previous = (
                ticket or previous or (active.first() if active.count() == 1 else None)
            )
            ticket = None
        if not ticket and (
            force_new
            or previous
            or (
                not active.exists()
                and (
                    conv.kind == "private"
                    or conv.mode == "single_customer"
                    or mentioned
                )
            )
        ):
            ticket = create_ticket(
                connection.workspace,
                conv,
                connection.default_team,
                text.removeprefix("/new").strip() or "Новое обращение",
                customer,
                previous=previous,
                thread_id=message.thread_id,
            )
        if ticket:
            bind(message, ticket)
        elif conv.kind == "private":
            buttons = [
                [
                    {
                        "text": f"№{t.number} · {t.subject[:35]}",
                        "callback_data": f"pick:{message.id}:{t.number}",
                    }
                ]
                for t in active[:10]
            ]
            buttons.append(
                [{"text": "Новая проблема", "callback_data": f"new:{message.id}"}]
            )
            automatic(
                conv,
                "К какому обращению относится сообщение?",
                {"inline_keyboard": buttons},
            )
        elif not (
            mentioned
            or force_new
            or conv.mode == "single_customer"
            or message.reply_id
            or message.thread_id
        ):
            message.metadata = {**message.metadata, "context_only": True}
            message.save(update_fields=["metadata"])
    event.state = "done"
    event.save(update_fields=["state"])
