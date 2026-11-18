import logging
import uuid
from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer
from django.db import transaction
from django.utils import timezone
from apps.workspaces.models import Membership
from apps.core.access import tickets_for
from .models import Notification


def publish(ticket, kind="ticket.changed"):
    data = {
        "id": str(uuid.uuid4()),
        "type": kind,
        "time": timezone.now().isoformat(),
        "object_id": str(ticket.id),
        "version": ticket.version,
    }
    workspace_id, ticket_id = ticket.workspace_id, ticket.id

    def send():
        try:
            layer = get_channel_layer()
            for m in Membership.objects.filter(
                workspace_id=workspace_id, active=True, user__is_active=True
            ):
                if tickets_for(m).filter(id=ticket_id).exists():
                    group, event = f"user_{m.user_id.hex}", {"type": "desk.event", "data": data}
                    from django.conf import settings
                    from apps.core.native_events import send_group
                    if settings.OWNDESK_RUNTIME != "native" or not send_group(layer, group, event):
                        async_to_sync(layer.group_send)(group, event)
        except Exception:
            logging.getLogger(__name__).warning(
                "Live event unavailable; clients recover through API"
            )

    transaction.on_commit(send)


def notify(ticket, kind, text):
    if ticket.assignee_id:
        Notification.objects.update_or_create(
            workspace=ticket.workspace,
            recipient_id=ticket.assignee.user_id,
            ticket=ticket,
            kind=kind,
            read_at=None,
            defaults={"text": text},
        )
